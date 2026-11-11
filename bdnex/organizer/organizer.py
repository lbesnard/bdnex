"""Write library metadata into files, convert them to CBZ and organise them on disk.

Modes (like ``beet import`` / ``beet move``):

* ``write``: update ``ComicInfo.xml`` where the file is (CBR/PDF become a CBZ next to the original),
* ``move``:  write, then rename into the library tree from the ``paths`` templates,
* ``copy``:  as ``move`` but the source is left untouched; the *copy* is what the database tracks.

Every new archive is built in a temporary file, verified (page count and bytes), and only then
put in place; the checksum stored in the database is refreshed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from bdnex.config import Settings
from bdnex.library import Library
from bdnex.organizer import archive, comicinfo, template

logger = logging.getLogger(__name__)

WRITE, MOVE, COPY = "write", "move", "copy"
WRITTEN, MOVED, COPIED, UNCHANGED, SKIPPED, FAILED, PRETEND = (
    "written", "moved", "copied", "unchanged", "skipped", "failed", "pretend")
TMP_SUFFIX = ".bdnex-tmp"

# template fields that can be saved back to the database when typed at a prompt
ALBUM_COLUMNS = {"title", "year", "language", "isbn", "publisher"}
CREDIT_ROLES = {"authors": "Writer", "writer": "Writer", "penciller": "Penciller", "colorist": "Colorist"}


class Prompter(Protocol):
    def conflict(self, path: str, diff_lines: list[str]) -> str:
        """The file's ComicInfo.xml differs from the database: ``database``, ``file`` or ``skip``."""

    def confirm(self, message: str) -> bool: ...

    def ask_value(self, field: str, label: str) -> Optional[str]:
        """Value for an empty template field (None/empty = skip the file)."""

    def collision(self, dest: str) -> str:
        """The destination exists: ``skip``, ``keep_both`` or ``replace``."""


@dataclass
class FileResult:
    path: str
    outcome: str
    dest: Optional[str] = None
    note: str = ""


def album_id_of(row: dict[str, Any]) -> Optional[int]:
    """Album id of a file row. ``ls --files`` rows carry it as ``id`` (the file is ``file_id``);
    ``Library.get_file`` rows carry it as ``album_id`` (and ``id`` is the file)."""
    return row["id"] if "file_id" in row else row.get("album_id")


class Organizer:
    def __init__(self, library: Library, settings: Settings, prompter: Optional[Prompter] = None,
                 pretend: bool = False, force: bool = False):
        self.library = library
        self.settings = settings
        self.prompter = prompter
        self.pretend = pretend
        self.force = force

    # ------------------------------------------------------------------ run
    def run(self, rows: list[dict[str, Any]], mode: str, progress=None) -> list[FileResult]:
        results = []
        for number, row in enumerate(rows, start=1):
            if progress:
                progress(f"[{number}/{len(rows)}] {os.path.basename(row['path'])}")
            try:
                results.append(self.process(row, mode))
            except Exception as err:  # one file must not stop the batch
                logger.exception(f"{row['path']} failed")
                results.append(FileResult(row["path"], FAILED, note=str(err)))
        return results

    # ------------------------------------------------------------- one file
    def process(self, row: dict[str, Any], mode: str) -> FileResult:
        path, fmt = row["path"], row["format"]
        album_id = album_id_of(row)
        if album_id is None:
            return FileResult(path, SKIPPED, note="not matched to an album: run `bdnex import`")
        if not os.path.exists(path):
            return FileResult(path, FAILED, note="file not found")
        if row.get("corrupted"):
            return FileResult(path, SKIPPED, note="corrupted archive")
        write = self.settings.write
        if fmt == "cbr" and write.cbr == "skip":
            return FileResult(path, SKIPPED, note="cbr skipped (write.cbr: skip)")
        if fmt == "pdf" and write.pdf == "skip":
            return FileResult(path, SKIPPED, note="pdf skipped (write.pdf: skip)")

        dest = self._destination(path, fmt, album_id, mode)
        if isinstance(dest, FileResult):
            return dest
        album = self.library.get_album(album_id)  # reload: a prompt may have completed it

        entries, existing = archive.read_entries(path, fmt, write.pdf_mode, write.pdf_dpi)
        new_fields = comicinfo.album_to_fields(album, archive.page_count(entries))
        xml = self._resolve_xml(path, existing, new_fields)
        if isinstance(xml, FileResult):
            return xml

        converted = fmt != "cbz"
        same_place = os.path.abspath(dest) == os.path.abspath(path)
        if same_place and not converted and xml == existing:
            return FileResult(path, UNCHANGED, dest, "ComicInfo.xml already up to date")

        steps = [f"convert {fmt} -> cbz"] if converted else []
        steps.append("write ComicInfo.xml" if xml != existing else "keep ComicInfo.xml")
        if not same_place:
            steps.append(f"{'copy' if mode == COPY else 'move'} to {dest}")
        if self.pretend:
            return FileResult(path, PRETEND, dest, "; ".join(steps))

        if not same_place and os.path.exists(dest):
            dest = self._resolve_collision(dest)
            if dest is None:
                return FileResult(path, SKIPPED, note="destination exists")

        tmp = dest + TMP_SUFFIX
        try:
            archive.write_cbz(tmp, entries, xml)
            archive.verify_cbz(tmp, entries, xml)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        os.replace(tmp, dest)

        removed = self._dispose_source(path, dest, mode, converted)
        self._update_library(path, dest)
        outcome = {WRITE: WRITTEN, MOVE: MOVED, COPY: COPIED}[mode]
        return FileResult(path, outcome, dest, "; ".join(steps) + ("" if removed or same_place else " (original kept)"))

    # -------------------------------------------------------------- helpers
    def _destination(self, path: str, fmt: str, album_id: int, mode: str):
        if mode == WRITE:
            return path if fmt == "cbz" else os.path.splitext(path)[0] + ".cbz"

        root = self.settings.require_library_directory()
        album = self.library.get_album(album_id)
        tpl = self.settings.paths.oneshot if album["kind"] == "oneshot" else self.settings.paths.series
        rendered = template.render(tpl, template.fields_for(album),
                                   lambda name: self._missing(name, album_id, album))
        if rendered.skipped_field:
            return FileResult(path, SKIPPED, note=f"no value for `{rendered.skipped_field}` in the path template")
        if not rendered.parts:
            return FileResult(path, SKIPPED, note="the path template is empty")
        *folders, name = rendered.parts
        return str(root.joinpath(*folders, name + ".cbz"))

    def _missing(self, field: str, album_id: int, album: dict) -> Optional[str]:
        policy = self.settings.paths.missing_field
        placeholder = self.settings.paths.placeholder
        if policy == "skip":
            return None
        if policy == "placeholder" or self.pretend or self.prompter is None or self.force:
            return placeholder
        value = (self.prompter.ask_value(field, album["title"]) or "").strip()
        if not value:
            return None
        self._persist(album_id, field, value)
        return value

    def _persist(self, album_id: int, field: str, value: str) -> None:
        if field in ALBUM_COLUMNS:
            self.library.set_album_fields(album_id, **{field: int(value) if field == "year" and value.isdigit() else value})
        elif field == "volume" and value.isdigit():
            self.library.set_album_fields(album_id, volume=int(value))
        elif field in CREDIT_ROLES:
            self.library.add_credit(album_id, CREDIT_ROLES[field], value)

    def _resolve_xml(self, path: str, existing: Optional[bytes], new_fields: dict[str, str]):
        """Bytes of the ComicInfo.xml to write, or a FileResult when the file is to be skipped."""
        if existing is None:
            return comicinfo.build_xml(new_fields)
        try:
            old_fields = comicinfo.parse_xml(existing)
        except Exception:
            return comicinfo.build_xml(new_fields)  # unreadable XML: nothing worth keeping
        diff = comicinfo.diff_fields(old_fields, new_fields)
        if not diff:
            return existing
        choice = self.settings.write.conflict
        if self.force:
            choice = "database"
        elif choice == "ask":
            if self.pretend:
                choice = "database"
            elif self.prompter is None:
                choice = "file"
            else:
                choice = self.prompter.conflict(path, diff)
        if choice == "skip":
            return FileResult(path, SKIPPED, note="ComicInfo.xml conflict: skipped")
        if choice == "file":
            return existing
        return comicinfo.build_xml(comicinfo.merge_with_existing(old_fields, new_fields))

    def _resolve_collision(self, dest: str) -> Optional[str]:
        if self.force:
            action = "keep_both"
        elif self.prompter is None:
            action = "skip"
        else:
            action = self.prompter.collision(dest)
        if action == "skip":
            return None
        if action == "replace":
            self.library.remove_file(dest)
            return dest
        base, ext = os.path.splitext(dest)
        n = 2
        while os.path.exists(f"{base} ({n}){ext}"):
            n += 1
        return f"{base} ({n}){ext}"

    def _dispose_source(self, src: str, dest: str, mode: str, converted: bool) -> bool:
        """Remove the original when appropriate. Returns True if it was removed."""
        if os.path.abspath(src) == os.path.abspath(dest) or mode == COPY:
            return False
        policy = self.settings.write.delete_original
        if converted:  # a cbr/pdf replaced by its cbz: honour delete_original
            if policy == "no" or (policy == "ask" and not self._confirm(f"Delete the original {src}?")):
                return False
        os.remove(src)
        return True

    def _confirm(self, message: str) -> bool:
        return bool(self.prompter and not self.force and self.prompter.confirm(message))

    def _update_library(self, src: str, dest: str) -> None:
        if os.path.abspath(src) == os.path.abspath(dest):
            self.library.refresh_file(dest)
        else:
            self.library.move_file(src, dest)
