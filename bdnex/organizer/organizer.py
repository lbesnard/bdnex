"""Write library metadata into files, convert them to CBZ and organise them on disk.

Modes (like ``beet import`` / ``beet move``):

* ``write``: update ``ComicInfo.xml`` where the file is (CBR/PDF become a CBZ next to the original),
* ``move``:  write, then rename into the library tree from the ``paths`` templates,
* ``copy``:  as ``move`` but the source is left untouched; the *copy* is what the database tracks.

Every new archive is built in a temporary file, verified (page count and bytes), and only then
put in place; the checksum stored in the database is refreshed.
"""

from __future__ import annotations

import datetime
import errno
import logging
import multiprocessing
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from bdnex import trash
from bdnex.config import Settings
from bdnex.formatting import human_size
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
        """The file's ComicInfo.xml differs from the database: ``database``, ``database_all`` (this one and every
        later one of the run), ``file``, ``fields`` or ``skip``."""

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

    def size_action(self, path: str, before: int, after: int, tolerance: float, drawn: int = 0) -> str:
        """The new archive is much bigger or smaller than the original: ``accept`` it or ``refuse`` it.
        ``drawn`` is how many PDF pages had to be drawn (they are not exact copies of the PDF's pictures)."""


class WriteAborted(RuntimeError):
    """The user chose to stop the run: what is done stays done, the remaining files are not touched."""


class SizeChanged(archive.ArchiveError):
    """The new archive does not have about the size of the original."""

    def __init__(self, before: int, after: int, drawn: int = 0):
        super().__init__(f"{human_size(before)} became {human_size(after)}")
        self.before, self.after, self.drawn = before, after, drawn


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
    seconds: float = 0.0  # how long the file took


def album_id_of(row: dict[str, Any]) -> Optional[int]:
    """Album id of a file row. ``ls --files`` rows carry it as ``id`` (the file is ``file_id``);
    ``Library.get_file`` rows carry it as ``album_id`` (and ``id`` is the file)."""
    return row["id"] if "file_id" in row else row.get("album_id")


class Deferred(Exception):
    """A decision is needed from the user: the file is done in the main process, where the questions are asked."""


class _Deferring:
    """Stands in for the prompter in a worker process: any question means "ask in the main process"."""

    def __getattr__(self, name):
        def ask(*args, **kwargs):
            raise Deferred(name)
        return ask


_WORKER: Optional["Organizer"] = None  # the organizer of this worker process (see :func:`_init_worker`)
_STATE = None  # shared memory, one byte per file: 0 waiting, 1 being worked on, 2 done (set before the workers are forked)


def _init_worker(settings, force: bool, batch: str) -> None:
    global _WORKER
    library = Library.for_settings(settings)  # its own connection: a connection is not shared with a forked process
    _WORKER = Organizer(library, settings, prompter=None if force else _Deferring(), force=force)
    _WORKER.batch = batch


def _failure(row, err, repair: bool = False) -> "FileResult":
    """A failed file's result. A damaged archive is expected: say so in one line, no traceback over the bar."""
    note = str(err)
    if isinstance(err, (archive.ArchiveError, OSError)):
        logger.debug(f"{row['path']} failed", exc_info=True)
        if isinstance(err, archive.ArchiveError) and not repair:
            note += " (`bdnex repair` rebuilds a file from its readable pages)"
    else:
        logger.exception(f"{row['path']} failed")
    return FileResult(row["path"], FAILED, note=note)


def _work(task):
    """One file in a worker: its result, or ``None`` when the main process has to do it (a question, an abort)."""
    index, row, mode, repair = task
    _STATE[index] = 1
    try:
        return _WORKER.repair(row) if repair else _WORKER.process(row, mode)
    except (Deferred, WriteAborted):
        return None
    except Exception as err:
        return _failure(row, err, repair)
    finally:
        _STATE[index] = 2


def auto_workers() -> int:
    """How many files are worked on at once when nothing says: the cores less one, at most 4 (a file is held in memory)."""
    return max(1, min(4, (os.cpu_count() or 2) - 1))


JUNK_FILES = {".DS_Store", "Thumbs.db", "thumbs.db", "desktop.ini", "Desktop.ini", ".directory"}


class Organizer:
    def __init__(self, library: Library, settings: Settings, prompter: Optional[Prompter] = None,
                 pretend: bool = False, force: bool = False):
        self.library = library
        self.settings = settings
        self.prompter = prompter
        self.aborted_at: Optional[str] = None  # the file being handled when the user aborted
        self.pretend = pretend
        self.force = force
        self.batch = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")  # what `bdnex undo` takes back together
        self.on_stage: Optional[Callable[[str, str], None]] = None  # (file, what is happening), for a live display
        self._current = ""
        self._database_wins = False  # "database wins for the rest of the queue" was answered

    # ------------------------------------------------------------------ run
    def run(self, rows: list[dict[str, Any]], mode: str, progress=None, on_result=None, workers: int = 1,
            on_busy=None, repair: bool = False) -> list[FileResult]:
        """Process the files in order. ``progress(text)`` is told which file starts, ``on_result(number, total, result)``
        gets each result as soon as it is known (so a long run shows what it does, not only at the end).

        With ``workers`` > 1 files that need no answer from you are worked on in several processes at once, and
        ``on_busy(names)`` is told, about twice a second, which ones are being worked on. A file that needs a decision
        (a conflict, a name clash, a missing value, a size change) is done here, in order, with the real questions."""
        if workers > 1 and len(rows) > 1 and not self.pretend and "fork" in multiprocessing.get_all_start_methods():
            return self._run_parallel(rows, mode, progress, on_result, workers, on_busy, repair)
        results = []
        for number, row in enumerate(rows, start=1):
            if progress:
                progress(f"[{number}/{len(rows)}] {os.path.basename(row['path'])}")
            try:
                result = self.repair(row) if repair else self.process(row, mode)
            except WriteAborted:
                self.aborted_at = row["path"]
                break
            except Exception as err:  # one file must not stop the batch
                result = _failure(row, err, repair)
            results.append(result)
            if on_result:
                on_result(number, len(rows), result)
        return results

    def _independent(self, rows: list[dict[str, Any]], mode: str, repair: bool = False) -> set[int]:
        """The positions of the files that can be worked on without looking at each other: their destinations are all
        different (and not the place another file is leaving), and finding them needs no question."""
        probe = Organizer(self.library, self.settings, prompter=_Deferring(), force=self.force)
        planned: dict[int, str] = {}
        for index, row in enumerate(rows):
            if not os.path.exists(row["path"]) or (not repair and (album_id_of(row) is None or row.get("corrupted"))):
                continue  # the main process explains these
            if repair:  # a repaired file is written beside (CBZ) or replaced by a CBZ of the same name
                planned[index] = row["path"] if row["format"] == "cbz" else os.path.splitext(row["path"])[0] + ".cbz"
                continue
            try:
                dest = probe._destination(row["path"], row["format"], album_id_of(row), mode, bool(row.get("spare")))
            except Exception:  # a question, or a template problem: the main process deals with it
                continue
            planned[index] = dest if isinstance(dest, str) else row["path"]
        taken: dict[str, int] = {}
        for index, dest in planned.items():
            taken[dest] = taken.get(dest, 0) + 1
        leaving = {os.path.abspath(row["path"]) for row in rows}
        return {index for index, dest in planned.items()
                if taken[dest] == 1 and (dest == rows[index]["path"] or os.path.abspath(dest) not in leaving)}

    def _run_parallel(self, rows, mode, progress, on_result, workers, on_busy, repair=False) -> list[FileResult]:
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures import TimeoutError as Waiting

        global _STATE
        parallel = self._independent(rows, mode, repair)
        results: list[FileResult] = []
        context = multiprocessing.get_context("fork")
        _STATE = context.Array("b", len(rows), lock=False)  # which files are being worked on, as the workers see it
        executor = ProcessPoolExecutor(max_workers=workers, mp_context=context,
                                       initializer=_init_worker, initargs=(self.settings, self.force, self.batch))
        futures = {index: executor.submit(_work, (index, rows[index], mode, repair)) for index in sorted(parallel)}
        try:
            for index, row in enumerate(rows):
                number = index + 1
                if progress:
                    progress(f"[{number}/{len(rows)}] {os.path.basename(row['path'])}")
                result = None
                try:
                    if index in futures:
                        while True:
                            try:
                                result = futures[index].result(timeout=0.5)
                                break
                            except Waiting:
                                if on_busy:
                                    on_busy([os.path.basename(rows[i]["path"]) for i in futures if _STATE[i] == 1])
                    if result is None:  # not independent, or a question to ask: here, in order
                        result = self.repair(row) if repair else self.process(row, mode)
                except WriteAborted:
                    self.aborted_at = row["path"]
                    break
                except Exception as err:
                    result = _failure(row, err, repair)
                results.append(result)
                if on_result:
                    on_result(number, len(rows), result)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)  # the files already started finish; the rest are not begun
        return results

    def _stage(self, text: str) -> None:
        """What is being done to the current file now: logged (``-v``) and shown by a live display."""
        logger.info(f"{os.path.basename(self._current)}: {text}")
        if self.on_stage:
            self.on_stage(self._current, text)

    # ------------------------------------------------------------- one file
    def _read_only(self, path: str, mode: str) -> Optional[FileResult]:
        """A file in a source that is only ever copied is never changed, moved or deleted: ``None`` if the operation
        is allowed, else the result that says why not."""
        source = self.settings.source_for(path)
        if source is None or source.mode != "copy" or mode == COPY:
            return None
        return FileResult(path, SKIPPED, note=f"in the read-only source {source.alias!r} (import.sources, mode copy): bdnex "
                                              "never changes it. `bdnex move --copy` copies it into the collection")

    def process(self, row: dict[str, Any], mode: str) -> FileResult:
        """One file, start to end (see :meth:`_process`); the result says how long it took."""
        started = time.monotonic()
        self._current = row["path"]
        result = self._process(row, mode)
        result.seconds = time.monotonic() - started
        return result

    def _process(self, row: dict[str, Any], mode: str) -> FileResult:
        path, fmt = row["path"], row["format"]
        refused = self._read_only(path, mode)
        if refused:
            return refused
        album_id = album_id_of(row)
        if album_id is None:
            return FileResult(path, SKIPPED, note="not matched to an album: run `bdnex import`")
        if not os.path.exists(path):
            return FileResult(path, FAILED, note="file not found")
        spare = bool(row.get("spare"))
        left_as_is = self._left_as_is(fmt)
        if self._tags_in_place(fmt):
            return self._tag_pdf(row, mode, album_id, spare)
        if left_as_is:  # no tags can be written, but `move` can still put the file where it belongs
            return self._move_as_is(path, fmt, album_id, mode, spare, left_as_is)

        recovered = None
        if row.get("corrupted"):  # try to repair first; ask what to do if that fails
            recovered = archive.read_entries_lenient(path, fmt)
            if not recovered.ok:
                return self._unrepairable(row, album_id, fmt, mode, recovered)
        repaired_pages = recovered.repaired if recovered else []

        dest = self._destination(path, fmt, album_id, mode, spare)
        if isinstance(dest, FileResult):
            return dest
        if not self.pretend:
            self._stage("reading the archive" + (" (a PDF: page by page)" if fmt == "pdf" else ""))
        entries, existing, pages = self._read(path, fmt, recovered)
        album = self.library.get_album(album_id)  # reload: a prompt may have completed it
        xml = self._resolve_xml(path, existing, comicinfo.album_to_fields(album, pages or 0), album_id)
        if isinstance(xml, FileResult):
            return xml

        converted = fmt != "cbz"
        same_place = os.path.abspath(dest) == os.path.abspath(path)
        if same_place and not converted and xml == existing and not repaired_pages:
            return FileResult(path, UNCHANGED, dest, "ComicInfo.xml already up to date")

        steps = self._plan(fmt, entries, xml != existing, repaired_pages, dest, mode, same_place)
        if self.pretend:
            where = "; stays where it is" if same_place else (
                "; the destination already exists (you would be asked what to do)" if os.path.exists(dest) else "")
            return FileResult(path, PRETEND, dest, "would: " + "; ".join(steps) + where)
        return self._carry_out(row, mode, dest, entries, xml, steps, converted, same_place, spare)

    def _left_as_is(self, fmt: str) -> Optional[str]:
        """Why a file of this format gets no tags (``None``: it is tagged)."""
        write = self.settings.write
        if self._tags_in_place(fmt):
            return None
        if fmt != "cbz" and write.format_preferred == "none":
            return f"{fmt} left as it is (write.format_preferred: none): only a CBZ can be tagged without converting it"
        if fmt == "cbr" and write.cbr == "skip":
            return "cbr skipped (write.cbr: skip)"
        if fmt == "pdf" and write.pdf == "skip":
            return "pdf skipped (write.pdf: skip)"
        return None

    def _tags_in_place(self, fmt: str) -> bool:
        """A PDF that keeps being a PDF: its tags are written into it (``write.pdf: tag``)."""
        return fmt == "pdf" and self.settings.write.pdf == "tag"

    def _tag_pdf(self, row: dict[str, Any], mode: str, album_id: int, spare: bool) -> FileResult:
        """Tag a PDF without converting it: Info dictionary, XMP and ComicInfo.xml attached, its pages untouched."""
        path = row["path"]
        dest = self._destination(path, "pdf", album_id, mode, spare)
        if isinstance(dest, FileResult):
            return dest
        try:
            existing, pages = archive.pdf_comicinfo(path)
        except archive.ArchiveError as err:
            return self._move_as_is(path, "pdf", album_id, mode, spare, f"this PDF can not be tagged ({err})")
        fields = comicinfo.album_to_fields(self.library.get_album(album_id), pages or 0)
        xml = self._resolve_xml(path, existing, fields, album_id)
        if isinstance(xml, FileResult):
            return xml
        same_place = os.path.abspath(dest) == os.path.abspath(path)
        if same_place and xml == existing:
            return FileResult(path, UNCHANGED, dest, "ComicInfo.xml already up to date")
        steps = ["tag the PDF in place (Info, XMP, ComicInfo.xml attached; its pages are not touched)"
                 if xml != existing else "keep the tags"]
        if not same_place:
            steps.append(f"{'copy' if mode == COPY else 'move'} to {dest}")
        if self.pretend:
            return FileResult(path, PRETEND, dest, "would: " + "; ".join(steps) + (
                "; stays where it is" if same_place else
                "; the destination already exists (you would be asked what to do)" if os.path.exists(dest) else ""))
        return self._carry_out(row, mode, dest, None, xml, steps, False, same_place, spare, pdf_fields=fields)

    def _build_pdf(self, path: str, dest: str, xml: bytes, fields: dict) -> None:
        """``dest`` is a tagged copy of ``path``, made beside it and put in place only when it checks out."""
        tmp = dest + TMP_SUFFIX
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            self._stage("copying the PDF to tag it (its pages are not touched)")
            shutil.copyfile(path, tmp)
            self._stage("writing the tags into the PDF")
            archive.tag_pdf(tmp, xml, fields)
            self._stage("checking the tagged PDF against the original")
            archive.verify_tagged_pdf(tmp, path, xml)
            self._stage("making sure it is on the disk")
            _flush_to_disk(tmp)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self._stage("putting it in place")
        os.replace(tmp, dest)

    def _read(self, path: str, fmt: str, recovered):
        """``(entries, existing ComicInfo.xml, page count)``. A preview does not read the pictures (that is what
        makes it instant): ``entries`` is then ``None``."""
        write = self.settings.write
        if recovered is not None:
            return recovered.entries, recovered.comicinfo, archive.page_count(recovered.entries)
        if self.pretend:
            existing, pages = archive.peek(path, fmt)
            return None, existing, pages
        entries, existing = archive.read_entries(path, fmt, write.pdf_mode, write.pdf_dpi, write.pdf_render_format,
                                              write.pdf_jpeg_quality)
        return entries, existing, archive.page_count(entries)

    def _plan(self, fmt, entries, xml_changes: bool, repaired_pages, dest: str, mode: str, same_place: bool) -> list[str]:
        """What is going to be done, in words (the report line of a real run, and of a preview)."""
        steps = [f"repair {len(repaired_pages)} page(s) with a wrong checksum"] if repaired_pages else []
        if fmt != "cbz":
            steps.append(f"convert {fmt} -> cbz")
        steps += self._page_notes(fmt, entries)
        steps.append("write ComicInfo.xml" if xml_changes else "keep ComicInfo.xml")
        if not same_place:
            if mode == WRITE:  # the converted file sits next to the original: nothing moves
                steps.append(f"save as {os.path.basename(dest)} (same folder)")
            else:
                steps.append(f"{'copy' if mode == COPY else 'move'} to {dest}")
        return steps

    @staticmethod
    def _page_notes(fmt: str, entries) -> list[str]:
        """How a PDF's pages were (or will be) taken out: untouched, or drawn."""
        if fmt != "pdf":
            return []
        if entries is None:
            return ["pages are checked on the real run (extracted untouched when possible)"]
        drawn = [number for number, entry in enumerate(entries, start=1) if entry.rendered]
        if drawn:
            return [f"{len(drawn)} of {len(entries)} page(s) had to be drawn, not extracted "
                    f"(page {', '.join(map(str, drawn[:8]))}{'...' if len(drawn) > 8 else ''})"]
        decoded = sum(1 for entry in entries if entry.decoded)
        return [f"all {len(entries)} pages extracted untouched" if not decoded else
                f"all {len(entries)} pages extracted without loss ({decoded} saved as PNG, same pixels)"]

    def _carry_out(self, row, mode, dest, entries, xml, steps, converted, same_place, spare,
                   pdf_fields: Optional[dict] = None) -> FileResult:
        """Write the new archive safely, then deal with the original and the library."""
        path = row["path"]
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
        delete = self._delete_wanted(path, dest, mode, converted)  # asked now, before anything is written
        try:
            if pdf_fields is not None:  # a PDF that stays a PDF
                self._build_pdf(path, dest, xml, pdf_fields)
            else:
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
            self._stage("checking the file that is now on disk, before the original may go")
            try:  # the file as it now is on disk, read afresh, before the original may go
                archive.verify_cbz(dest, entries, xml)
            except archive.ArchiveError as err:
                os.remove(dest)
                return FileResult(path, FAILED, note=f"the new archive failed the final check ({err}): "
                                                    "the original is kept")

        old_row = self.library.get_file(path)
        if mode != COPY and not same_place:
            self._stage("removing the original" if delete is not False else "keeping the original")
        removed = self._dispose_source(path, dest, mode, converted, delete)
        self._stage("updating the library")
        self._update_library(path, dest)
        self._remember_copy(path, dest, mode)
        if removed == "trash":
            self._send_to_trash(path, old_row, f"converted to {os.path.basename(dest)}")
            steps.append("original moved to the trash")
        if spare and not row.get("spare"):
            self.library.set_file_fields(dest, spare=1)
        if removed and mode == MOVE:
            if not converted and not same_place:
                self.library.record_move(self.batch, path, dest)  # a plain move can be taken back
            self._prune_empty_dirs(path)
        outcome = {WRITE: WRITTEN, MOVE: MOVED, COPY: COPIED}[mode]
        return FileResult(path, outcome, dest, "; ".join(steps) + ("" if removed or same_place else " (original kept)"))

    # --------------------------------------------------------------- repairing
    def repair(self, row: dict[str, Any]) -> FileResult:
        """Rebuild a damaged CBZ/CBR as a clean CBZ, keeping its ComicInfo.xml and pages untouched; the result says how
        long it took."""
        started = time.monotonic()
        self._current = row["path"]
        result = self._repair(row)
        result.seconds = time.monotonic() - started
        return result

    def _repair(self, row: dict[str, Any]) -> FileResult:
        path, fmt = row["path"], row["format"]
        refused = self._read_only(path, WRITE)
        if refused:
            return refused
        if not os.path.exists(path):
            return FileResult(path, FAILED, note="file not found")
        if not row.get("corrupted"):
            return FileResult(path, UNCHANGED, note="not damaged")
        if fmt != "cbz" and self.settings.write.format_preferred == "none":
            return FileResult(path, SKIPPED, note=f"{fmt} left as it is (write.format_preferred: none): "
                                                  "repairing it would convert it to CBZ")
        if not self.pretend:
            self._stage("reading the damaged archive, page by page")
        recovered = archive.read_entries_lenient(path, fmt)
        if not recovered.ok:
            return FileResult(path, FAILED, note="can not be repaired: " + "; ".join(recovered.lost))
        dest = path if fmt == "cbz" else os.path.splitext(path)[0] + ".cbz"
        note = f"{len(recovered.repaired)} page(s) with a wrong checksum rebuilt: {', '.join(recovered.repaired)}"
        if self.pretend:
            return FileResult(path, PRETEND, dest, "would repair: " + note)
        if dest != path and os.path.exists(dest):
            return FileResult(path, SKIPPED, note=f"{dest} already exists")
        delete = dest != path and self.settings.write.delete_original != "no" and (  # asked before anything is written
            self.settings.write.delete_original == "yes" or self._confirm(f"Delete the damaged original {path}?"))
        self._build(dest, recovered.entries, recovered.comicinfo)
        old_row = self.library.get_file(path)
        self._stage("updating the library")
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
                                                   self.settings.write.size_tolerance, change.drawn)
        if policy == "accept":
            return None
        return FileResult(path, SKIPPED, note=f"the new archive's size is off ({change}; write.size_tolerance is "
                                              f"{self.settings.write.size_tolerance:g}%): the original is untouched")

    def _build(self, dest: str, entries, xml: Optional[bytes], compare_to: Optional[str] = None) -> None:
        """Write ``dest`` through a verified temporary file; with ``compare_to``, its size must be about that file's."""
        tmp = dest + TMP_SUFFIX
        try:
            self._stage("writing the new archive (the pages as they are, and ComicInfo.xml)")
            archive.write_cbz(tmp, entries, xml)
            self._stage("checking the new archive against the original pages")
            archive.verify_cbz(tmp, entries, xml)
            self._stage("making sure it is on the disk")
            _flush_to_disk(tmp)
            tolerance = self.settings.write.size_tolerance
            if compare_to is not None and tolerance > 0 and os.path.exists(compare_to):
                before, after = os.path.getsize(compare_to), os.path.getsize(tmp)
                if abs(after - before) > max(before * tolerance / 100, MIN_SIZE_CHANGE):
                    raise SizeChanged(before, after, sum(1 for entry in entries if entry.rendered))
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self._stage("putting it in place")
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
        return self._move_as_is(path, fmt, album_id, mode, bool(row.get("spare")), note)

    def _move_as_is(self, path: str, fmt: str, album_id: int, mode: str, spare: bool, why: str) -> FileResult:
        """Put a file we can not tag (damaged, or left in its format by the config) where it belongs, byte for
        byte and with its own extension. A tags-only run (`write`) just leaves it."""
        if mode == WRITE:
            return FileResult(path, SKIPPED, note=why)
        dest = self._destination(path, fmt, album_id, mode, spare, as_is=True)
        if isinstance(dest, FileResult):
            return dest
        if os.path.abspath(dest) == os.path.abspath(path):
            return FileResult(path, UNCHANGED, dest, why + " (already in place)")
        if self.pretend:
            return FileResult(path, PRETEND, dest, f"would: move as it is, no tags written ({why})")
        if os.path.exists(dest):
            dest, became_spare = self._resolve_collision(dest, path, mode, spare)
            if dest is None:
                return FileResult(path, SKIPPED, note="destination exists")
            spare = spare or became_spare
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if mode == COPY:
            shutil.copy2(path, dest)
        else:
            shutil.move(path, dest)
        self._update_library(path, dest)
        self._remember_copy(path, dest, mode)
        if spare:
            self.library.set_file_fields(dest, spare=1)
        if mode == MOVE:
            self.library.record_move(self.batch, path, dest)
            self._prune_empty_dirs(path)
        return FileResult(path, COPIED if mode == COPY else MOVED, dest, why + " (moved as it is: no tags written)")

    # -------------------------------------------------------------- helpers
    def _destination(self, path: str, fmt: str, album_id: int, mode: str, spare: bool = False, as_is: bool = False):
        tagged_pdf = self._tags_in_place(fmt)  # it stays a PDF, with its own extension
        if mode == WRITE:
            return path if fmt == "cbz" or tagged_pdf else os.path.splitext(path)[0] + ".cbz"

        root = self.settings.require_collection()
        album = self.library.get_album(album_id)
        tpl = self.settings.paths.oneshot if album["kind"] == "oneshot" else self.settings.paths.series
        for rule in self.settings.paths.rules:  # the first rule whose query matches this album wins
            if self.library.album_matches(album_id, rule.match):
                tpl = rule.template
                break
        rendered = template.render(tpl, template.fields_for(album),
                                   lambda name: self._missing(name, album_id, album))
        if rendered.skipped_field:
            return FileResult(path, SKIPPED, note=f"no value for `{rendered.skipped_field}` in the path template")
        if not rendered.parts:
            return FileResult(path, SKIPPED, note="the path template is empty")
        *folders, name = rendered.parts
        extension = f".{fmt}" if as_is or tagged_pdf else ".cbz"  # an untouched file keeps what it is
        if spare:  # an extra version of the album: same name, in the spare versions folder
            return str(root.joinpath(self.settings.duplicates.versions_dir, *folders, name + extension))
        return str(root.joinpath(*folders, name + extension))

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
        if self.force or self._database_wins:
            choice = "database"
        elif choice == "ask":
            if self.pretend:
                choice = "database"
            elif self.prompter is None:
                choice = "file"
            else:
                choice = self.prompter.conflict(path, diff)
                if choice == "database_all":  # also the files that come after this one
                    self._database_wins, choice = True, "database"
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
        root = self.settings.require_collection()
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

    def _delete_wanted(self, src: str, dest: str, mode: str, converted: bool) -> Optional[bool]:
        """Is the original of a converted file to be deleted (``write.delete_original``)? Asked **before** the new file is
        written, so that a question never comes in the middle of the work. ``None``: it is not a question here."""
        if os.path.abspath(src) == os.path.abspath(dest) or mode == COPY or not converted:
            return None
        policy = self.settings.write.delete_original
        return policy != "no" and (policy == "yes" or self._confirm(f"Delete the original {src}?"))

    def _dispose_source(self, src: str, dest: str, mode: str, converted: bool, delete: Optional[bool] = None):
        """Remove the original when appropriate. Returns ``True`` if it was deleted, ``"trash"`` if it is to be
        moved to the trash (the caller does it once the library points at the new file), ``False`` if it stays."""
        if os.path.abspath(src) == os.path.abspath(dest) or mode == COPY:
            return False
        if converted:  # a cbr/pdf replaced by its cbz: honour delete_original
            if delete is None:
                delete = self._delete_wanted(src, dest, mode, converted)
            if not delete:
                return False
            if self.settings.write.use_trash:
                return "trash"  # moved once the library knows the new path
        os.remove(src)
        return True

    def _remember_copy(self, source: str, dest: str, mode: str) -> None:
        """A copy remembers where it came from (and how that file looked), so that importing the source again skips it."""
        if mode != COPY or os.path.abspath(source) == os.path.abspath(dest):
            return
        try:
            stat = os.stat(source)
            self.library.record_origin(dest, source, stat.st_size, stat.st_mtime)
        except (OSError, KeyError) as err:  # a record that fails must not undo a good copy
            logger.warning(f"could not record where {dest} was copied from: {err}")

    def _prune_empty_dirs(self, old_path: str) -> None:
        """Remove the folder a file left when nothing but system leftovers (.DS_Store...) remain in it, and the
        parents that this empties. Inside the library folder this goes up to (never including) the library root;
        elsewhere (an inbox) only the file's own folder goes, so a download folder is never climbed out of."""
        folder = os.path.dirname(os.path.abspath(old_path))
        try:
            root = os.path.abspath(os.path.expanduser(self.settings.require_collection()))
        except Exception:
            root = None
        inside = root is not None and folder.startswith(root + os.sep)
        while folder and folder != root and os.path.dirname(folder) != folder:
            try:
                names = os.listdir(folder)
                if any(name not in JUNK_FILES and not name.startswith("._") for name in names):
                    return
                for name in names:
                    os.remove(os.path.join(folder, name))
                os.rmdir(folder)
            except OSError:
                return  # not empty after all, a folder inside, or no right to: leave it
            logger.debug(f"removed the empty folder {folder}")
            if not inside:
                return
            folder = os.path.dirname(folder)

    def _confirm(self, message: str) -> bool:
        return bool(self.prompter and not self.force and self.prompter.confirm(message))

    def _update_library(self, src: str, dest: str) -> None:
        if os.path.abspath(src) == os.path.abspath(dest):
            self.library.refresh_file(dest)
        else:
            self.library.move_file(src, dest)
