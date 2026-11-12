"""Write library metadata into files, convert them to CBZ and organise them on disk.

Modes (like ``beet import`` / ``beet move``):

* ``write``: update ``ComicInfo.xml`` where the file is (CBR/PDF become a CBZ next to the original),
* ``move``:  write, then rename into the library tree from the ``paths`` templates,
* ``copy``:  as ``move`` but the source is left untouched; the *copy* is what the database tracks.

Every new archive is built in a temporary file, verified (page count and bytes), and only then
put in place; the checksum stored in the database is refreshed.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

from bdnex import trash
from bdnex.config import Settings
from bdnex.library import Library
from bdnex.organizer import archive, comicinfo, template
from bdnex.versions import compare, facts_of

logger = logging.getLogger(__name__)

WRITE, MOVE, COPY = "write", "move", "copy"
WRITTEN, MOVED, COPIED, UNCHANGED, SKIPPED, FAILED, PRETEND, REPAIRED = (
    "written", "moved", "copied", "unchanged", "skipped", "failed", "pretend", "repaired")
TMP_SUFFIX = ".bdnex-tmp"
ROOM_FACTOR = 1.25  # the new archive is expected to take at most this much of the original's size
MIN_SIZE_CHANGE = 1024 * 1024  # a difference under 1 MiB is never worth a question (tags on a tiny file)

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

    def corrupted_action(self, path: str, problems: list[str], can_move: bool) -> str:
        """A damaged archive could not be repaired: ``leave`` it as it is, or ``move`` it as is."""

    def resolve_fields(self, path: str, changes: list, file_fields: dict[str, str],
                       db_fields: dict[str, str]) -> Optional[dict[str, str]]:
        """Decide field by field: ``{field: final value}`` for the differing fields, ``None`` to skip the file."""

    def size_action(self, path: str, before: int, after: int, tolerance: float) -> str:
        """The new archive is much bigger or smaller than the original: ``accept`` it or ``refuse`` it."""


class WriteAborted(RuntimeError):
    """The user chose to stop the run: what is done stays done, the remaining files are not touched."""


class SizeChanged(archive.ArchiveError):
    """The new archive does not have about the size of the original."""

    def __init__(self, before: int, after: int):
        super().__init__(f"{human_size(before)} became {human_size(after)}")
        self.before, self.after = before, after


def human_size(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"


class NoRoom(archive.ArchiveError):
    """There is not enough free space to write safely."""


def free_space(path: str) -> int:
    """Bytes free on the disk that holds ``path`` (or the nearest existing parent of it)."""
    folder = os.path.abspath(path)
    while folder and not os.path.exists(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return shutil.disk_usage(folder).free


def _flush_to_disk(path: str) -> None:
    """Make sure the bytes are on the disk, not only in the cache, before the file takes its place."""
    try:
        with open(path, "rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        pass  # not every file system allows it; the checks that matter were already made


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
        self.aborted_at: Optional[str] = None  # the file being handled when the user aborted
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
            except WriteAborted:
                self.aborted_at = row["path"]
                break
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
        write = self.settings.write
        if fmt != "cbz" and write.format_preferred == "none":
            return FileResult(path, SKIPPED, note=f"{fmt} left as it is (write.format_preferred: none): "
                                                  "only a CBZ can be tagged without converting it")
        if fmt == "cbr" and write.cbr == "skip":
            return FileResult(path, SKIPPED, note="cbr skipped (write.cbr: skip)")
        if fmt == "pdf" and write.pdf == "skip":
            return FileResult(path, SKIPPED, note="pdf skipped (write.pdf: skip)")

        repaired_pages: list[str] = []
        recovered = None
        if row.get("corrupted"):  # try to repair first; ask what to do if that fails
            recovered = archive.read_entries_lenient(path, fmt)
            if not recovered.ok:
                return self._unrepairable(row, album_id, fmt, mode, recovered)
            repaired_pages = recovered.repaired

        spare = bool(row.get("spare"))
        dest = self._destination(path, fmt, album_id, mode, spare)
        if isinstance(dest, FileResult):
            return dest
        album = self.library.get_album(album_id)  # reload: a prompt may have completed it

        if recovered is not None:
            entries, existing = recovered.entries, recovered.comicinfo
            pages = archive.page_count(entries)
        elif self.pretend:  # a preview does not read the pictures: that is what makes it instant
            entries = None
            existing, pages = archive.peek(path, fmt)
        else:
            entries, existing = archive.read_entries(path, fmt, write.pdf_mode, write.pdf_dpi, write.pdf_render_format)
            pages = archive.page_count(entries)
        new_fields = comicinfo.album_to_fields(album, pages or 0)
        xml = self._resolve_xml(path, existing, new_fields, album_id)
        if isinstance(xml, FileResult):
            return xml

        converted = fmt != "cbz"
        same_place = os.path.abspath(dest) == os.path.abspath(path)
        if same_place and not converted and xml == existing and not repaired_pages:
            return FileResult(path, UNCHANGED, dest, "ComicInfo.xml already up to date")

        steps = [f"repair {len(repaired_pages)} page(s) with a wrong checksum"] if repaired_pages else []
        steps += [f"convert {fmt} -> cbz"] if converted else []
        drawn = [number for number, entry in enumerate(entries or [], start=1) if entry.rendered]
        if entries is None:
            if fmt == "pdf":
                steps.append("pages are checked on the real run (extracted untouched when possible)")
        elif drawn:
            steps.append(f"{len(drawn)} of {len(entries)} page(s) had to be drawn, not extracted "
                         f"(page {', '.join(map(str, drawn[:8]))}{'...' if len(drawn) > 8 else ''})")
        elif fmt == "pdf":
            decoded = sum(1 for entry in entries if entry.decoded)
            steps.append(f"all {len(entries)} pages extracted untouched" if not decoded else
                         f"all {len(entries)} pages extracted without loss ({decoded} saved as PNG, same pixels)")
        steps.append("write ComicInfo.xml" if xml != existing else "keep ComicInfo.xml")
        if not same_place:
            if mode == WRITE:  # the converted file sits next to the original: nothing moves
                steps.append(f"save as {os.path.basename(dest)} (same folder)")
            else:
                steps.append(f"{'copy' if mode == COPY else 'move'} to {dest}")
        if self.pretend:
            where = "" if not same_place else "; stays where it is"
            return FileResult(path, PRETEND, dest, "would: " + "; ".join(steps) + where)

        if not same_place and os.path.exists(dest):
            dest, became_spare = self._resolve_collision(dest, path, mode, spare)
            if dest is None:
                return FileResult(path, SKIPPED, note="destination exists")
            if became_spare:
                spare = True
                steps.append(f"kept as a spare version in {self.settings.duplicates.versions_dir}")

        problem = self._room_problem(dest, int(os.path.getsize(path) * ROOM_FACTOR))
        if problem:
            return FileResult(path, FAILED, note=problem)
        try:
            self._build(dest, entries, xml, compare_to=path)
        except OSError as err:
            if err.errno != errno.ENOSPC:
                raise
            return FileResult(path, FAILED, note="the disk is full: the partial file was removed, "
                                                "the original is untouched")
        except SizeChanged as change:
            refused = self._size_decision(path, change)
            if refused is not None:
                return refused
            self._build(dest, entries, xml)  # accepted: no second comparison
            steps.append(f"size {change}, accepted")
        else:
            if converted:
                steps.append(f"size {human_size(os.path.getsize(path))} -> {human_size(os.path.getsize(dest))}")

        if converted and not same_place and mode != COPY:
            try:  # the file as it now is on disk, read afresh, before the original may go
                archive.verify_cbz(dest, entries, xml)
            except archive.ArchiveError as err:
                os.remove(dest)
                return FileResult(path, FAILED, note=f"the new archive failed the final check ({err}): "
                                                    "the original is kept")

        old_row = self.library.get_file(path)
        removed = self._dispose_source(path, dest, mode, converted)
        self._update_library(path, dest)
        if removed == "trash":
            self._send_to_trash(path, old_row, f"converted to {os.path.basename(dest)}")
        if spare and not row.get("spare"):
            self.library.set_file_fields(dest, spare=1)
        outcome = {WRITE: WRITTEN, MOVE: MOVED, COPY: COPIED}[mode]
        if removed == "trash":
            steps.append("original moved to the trash")
        return FileResult(path, outcome, dest, "; ".join(steps) + ("" if removed or same_place else " (original kept)"))

    # --------------------------------------------------------------- repairing
    def repair(self, row: dict[str, Any]) -> FileResult:
        """Rebuild a damaged CBZ/CBR as a clean CBZ, keeping its ComicInfo.xml and pages untouched."""
        path, fmt = row["path"], row["format"]
        if not os.path.exists(path):
            return FileResult(path, FAILED, note="file not found")
        if not row.get("corrupted"):
            return FileResult(path, UNCHANGED, note="not damaged")
        if fmt != "cbz" and self.settings.write.format_preferred == "none":
            return FileResult(path, SKIPPED, note=f"{fmt} left as it is (write.format_preferred: none): "
                                                  "repairing it would convert it to CBZ")
        recovered = archive.read_entries_lenient(path, fmt)
        if not recovered.ok:
            return FileResult(path, FAILED, note="can not be repaired: " + "; ".join(recovered.lost))
        dest = path if fmt == "cbz" else os.path.splitext(path)[0] + ".cbz"
        note = f"{len(recovered.repaired)} page(s) with a wrong checksum rebuilt: {', '.join(recovered.repaired)}"
        if self.pretend:
            return FileResult(path, PRETEND, dest, "would repair: " + note)
        if dest != path and os.path.exists(dest):
            return FileResult(path, SKIPPED, note=f"{dest} already exists")
        self._build(dest, recovered.entries, recovered.comicinfo)
        old_row = self.library.get_file(path)
        delete = dest != path and self.settings.write.delete_original != "no" and (
            self.settings.write.delete_original == "yes" or self._confirm(f"Delete the damaged original {path}?"))
        self._update_library(path, dest)
        if delete:
            if self.settings.write.use_trash:
                self._send_to_trash(path, old_row, f"repaired as {os.path.basename(dest)}")
            else:
                os.remove(path)
        return FileResult(path, REPAIRED, dest, note)

    def _room_problem(self, dest: str, needed: int) -> Optional[str]:
        """Why writing ``needed`` bytes at ``dest`` is not safe (``None`` when it is): the disk of the file and
        the disk of the database must both keep ``write.min_free_space`` MiB free."""
        keep = self.settings.write.min_free_space * 1024 * 1024
        if keep == 0:
            return None
        spots = [(dest, needed, "the disk of the comics")]
        if self.library.path != ":memory:":
            spots.append((self.library.path, 0, "the disk of the library database"))
        for where, extra, label in spots:
            free = free_space(where)
            if free < extra + keep:
                return (f"not enough free space on {label}: {human_size(free)} free, "
                        f"{human_size(extra + keep)} needed (write.min_free_space is "
                        f"{self.settings.write.min_free_space} MiB); nothing was written")
        return None

    def _size_decision(self, path: str, change: SizeChanged) -> Optional[FileResult]:
        """``None`` to go on with the new archive, else the result that leaves the original alone."""
        policy = self.settings.write.size_change
        if policy == "ask":
            if self.force:
                policy = "accept"
            elif self.prompter is None:
                policy = "refuse"
            else:
                policy = self.prompter.size_action(path, change.before, change.after,
                                                   self.settings.write.size_tolerance)
        if policy == "accept":
            return None
        return FileResult(path, SKIPPED, note=f"the new archive's size is off ({change}; write.size_tolerance is "
                                              f"{self.settings.write.size_tolerance:g}%): the original is untouched")

    def _build(self, dest: str, entries, xml: Optional[bytes], compare_to: Optional[str] = None) -> None:
        """Write ``dest`` through a verified temporary file; with ``compare_to``, its size must be about that file's."""
        tmp = dest + TMP_SUFFIX
        try:
            archive.write_cbz(tmp, entries, xml)
            archive.verify_cbz(tmp, entries, xml)
            _flush_to_disk(tmp)
            tolerance = self.settings.write.size_tolerance
            if compare_to is not None and tolerance > 0 and os.path.exists(compare_to):
                before, after = os.path.getsize(compare_to), os.path.getsize(tmp)
                if abs(after - before) > max(before * tolerance / 100, MIN_SIZE_CHANGE):
                    raise SizeChanged(before, after)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        os.replace(tmp, dest)

    def _unrepairable(self, row, album_id, fmt, mode, recovered) -> FileResult:
        path = row["path"]
        problems = recovered.lost
        note = "can not be repaired: " + "; ".join(problems)
        can_move = mode != WRITE
        choice = self.settings.write.corrupted
        if choice == "ask":
            if self.pretend:
                return FileResult(path, PRETEND, note=note + " (you would be asked: leave it, or move it as it is)")
            if self.force or self.prompter is None:
                choice = "leave"
            else:
                choice = self.prompter.corrupted_action(path, problems, can_move)
        if choice != "move" or not can_move:
            return FileResult(path, SKIPPED, note=note + " (left as it is)")
        dest = self._destination(path, fmt, album_id, mode)
        if isinstance(dest, FileResult):
            return dest
        if self.pretend:
            return FileResult(path, PRETEND, dest, note + f" (would be moved as it is to {dest})")
        if os.path.abspath(dest) != os.path.abspath(path) and os.path.exists(dest):
            dest = self._resolve_collision(dest)
            if dest is None:
                return FileResult(path, SKIPPED, note="destination exists")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if mode == COPY:
            shutil.copy2(path, dest)
        elif os.path.abspath(dest) != os.path.abspath(path):
            shutil.move(path, dest)
        self._update_library(path, dest)
        return FileResult(path, COPIED if mode == COPY else MOVED, dest,
                          note + " (moved as it is: no tags written)")

    # -------------------------------------------------------------- helpers
    def _destination(self, path: str, fmt: str, album_id: int, mode: str, spare: bool = False):
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
        if spare:  # an extra version of the album: same name, in the spare versions folder
            return str(root.joinpath(self.settings.duplicates.versions_dir, *folders, name + ".cbz"))
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

    def _resolve_xml(self, path: str, existing: Optional[bytes], new_fields: dict[str, str], album_id: int):
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
        if choice == "fields":
            decide = getattr(self.prompter, "resolve_fields", None)
            chosen = decide(path, diff, old_fields, new_fields) if decide else new_fields
            if chosen is None:
                choice = "skip"
            else:
                final = {**old_fields, **new_fields, **chosen}
                self._keep_in_library(album_id, {c.field: final[c.field] for c in diff if c.field in final}, new_fields)
                return comicinfo.build_xml(final)
        if choice == "skip":
            return FileResult(path, SKIPPED, note="ComicInfo.xml conflict: skipped")
        if choice == "file":
            self._keep_in_library(album_id, {c.field: old_fields[c.field] for c in diff if c.field in old_fields},
                                  new_fields)
            return existing
        return comicinfo.build_xml(comicinfo.merge_with_existing(old_fields, new_fields))

    def _keep_in_library(self, album_id: int, values: dict[str, str], database: dict[str, str]) -> None:
        """What was chosen over the database's value becomes the library's: a hand edit (so it survives a
        re-import), with the fetched page still stored untouched. Not done for a preview."""
        if self.pretend:
            return
        differing = {field: value for field, value in values.items() if database.get(field) != value}
        changes = comicinfo.fields_to_changes(differing)
        if changes:
            try:
                self.library.edit_album(album_id, changes)
            except Exception as err:  # the file is still written: the library edit is the lesser matter
                logger.warning(f"could not keep the chosen values in the library: {err}")

    def _versions_path(self, dest: str) -> str:
        """Where the spare version of the file that belongs at ``dest`` goes."""
        root = self.settings.require_library_directory()
        try:
            relative = Path(dest).relative_to(root)
        except ValueError:
            return dest
        return str(root / self.settings.duplicates.versions_dir / relative)

    def _resolve_collision(self, dest: str, src: str, mode: str, spare: bool) -> tuple[Optional[str], bool]:
        """The name ``dest`` is taken. Returns (the path to write, whether the file becomes a spare
        version); ``None`` to leave the file alone."""
        if spare:  # spare versions are numbered, never asked about
            return str(trash.unique(Path(dest))), False
        if self.force:
            action = "keep_both"
        elif self.prompter is None or self.pretend:
            action = "skip"
        else:
            chooser = getattr(self.prompter, "resolve_collision", None)
            if chooser is None:
                action = self.prompter.collision(dest)
            else:
                existing, new = facts_of(self.library, dest), facts_of(self.library, src)
                action = chooser(compare([existing], new, self.settings.duplicates.prefer), self.library.root)
        if action == "skip":
            return None, False
        if action == "replace":
            trash.move_to_trash(self.library, self.settings, dest, f"replaced by {src}")
            return dest, False
        if mode == WRITE:  # tags only: nothing leaves the folder, the new name just gets a number
            return str(trash.unique(Path(dest))), False
        return str(trash.unique(Path(self._versions_path(dest)))), True

    def _send_to_trash(self, path: str, row: Optional[dict], reason: str) -> None:
        trash.move_to_trash(self.library, self.settings, path, reason, row=row, forget=False)

    def _dispose_source(self, src: str, dest: str, mode: str, converted: bool):
        """Remove the original when appropriate. Returns ``True`` if it was deleted, ``"trash"`` if it is to be
        moved to the trash (the caller does it once the library points at the new file), ``False`` if it stays."""
        if os.path.abspath(src) == os.path.abspath(dest) or mode == COPY:
            return False
        policy = self.settings.write.delete_original
        if converted:  # a cbr/pdf replaced by its cbz: honour delete_original
            if policy == "no" or (policy == "ask" and not self._confirm(f"Delete the original {src}?")):
                return False
            if self.settings.write.use_trash:
                return "trash"  # moved once the library knows the new path
        os.remove(src)
        return True

    def _confirm(self, message: str) -> bool:
        return bool(self.prompter and not self.force and self.prompter.confirm(message))

    def _update_library(self, src: str, dest: str) -> None:
        if os.path.abspath(src) == os.path.abspath(dest):
            self.library.refresh_file(dest)
        else:
            self.library.move_file(src, dest)
