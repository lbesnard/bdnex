"""The import session: match files to albums and record the result in the library.

Pipeline: file names are matched in the background (``pipeline.match_ahead``) while the user
reviews earlier files. Per file: skip if already imported -> corruption/duplicate-file checks ->
candidates -> score -> (ask) -> link the file to the album as known from the sitemap alone.
The album page of a linked file, then its series page, is requested at once in the background
(``Bedetheque.page_queue``: threads download, only this thread touches the library), politely and slowly. The
album's record is built from the stored pages as soon as both are here (``_handle_arrivals``: no request, covers
later); what is still on its way is waited for after the loop. An interrupted fetch loses nothing (``import --fetch``
resumes).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Optional

from bdnex import trash
from bdnex.config import Settings
from bdnex.importer import covers, folders
from bdnex.importer.matching import Match, Recommendation, find_matches, recommend
from bdnex.importer.pipeline import match_ahead, resolve_workers
from bdnex.importer.source import Candidate, MetadataSource
from bdnex.formatting import human_duration
from bdnex.library import Library
from bdnex.library.database import analyse_file
from bdnex.library.hashing import file_md5
from bdnex.library.scan import iter_comic_files
from bdnex.versions import compare, facts_of

logger = logging.getLogger(__name__)

# The result types and exceptions live in session_types (the cover and early-fetch parts need them too); they are
# re-exported here because this is where the rest of bdnex, and its tests, have always imported them from.
from bdnex.importer.session_covers import CoverPass  # noqa: E402
from bdnex.importer.session_vote import CoverVote  # noqa: E402
from bdnex.importer.session_early import EarlyFetch  # noqa: E402
from bdnex.importer.session_types import (  # noqa: E402,F401
    ALREADY, DUPLICATE, FAILED, OUTCOME_LABELS, PARTIAL, PRETEND, SKIPPED, STEPS, TAGGED, GoBack, ImportAborted,
    ImportReport, ImportResult, Journal, NeedsTerminal, Prompter, SkipFolder, album_label)



class ImportSession(EarlyFetch, CoverPass, CoverVote):
    """Import files in steps: ``match`` (file -> album, offline) and ``fetch`` (album -> metadata).

    ``steps`` selects them; both by default. A step that is already done for a file is skipped,
    unless ``force`` asks for it to be redone. ``auto`` is ``None`` (the user checks every file),
    ``"strong"`` (matches at or above ``match.strong_threshold`` are applied unasked) or ``"all"``
    (nothing is asked: strong and medium matches are applied, the rest skipped).
    """

    def __init__(self, library: Library, source: MetadataSource, settings: Settings,
                 prompter: Optional[Prompter] = None, pretend: bool = False, force: bool = False,
                 auto=None, steps=STEPS, progress: Optional[Callable[[str], None]] = None,
                 workers: Optional[int] = None, bar: Optional[Callable] = None,
                 cover_check: Optional[bool] = None, series: Optional[str] = None,
                 phase: Optional[Callable[[str, str], None]] = None, retry_skipped: bool = False,
                 files_untouched: bool = False, verbosity: int = 1,
                 pinned: Optional[Candidate] = None, duplicates: Optional[str] = None, refetch: Optional[bool] = None):
        self.library = library
        self._fresh: set[str] = set()  # album pages the queue has just downloaded
        self.refetch = refetch  # None: --force also asks the site again; False: match again but read the stored pages
        self.duplicates = duplicates  # `--keep-both`: this file is linked even when the album has another file (a spare version)
        self._loop_started = time.monotonic()
        self.verbosity = verbosity  # 0: a count now and then and what needs a look; 1 (-v): every file and why; 2 (-vv): + logging
        self.source = source
        self.settings = settings
        self.prompter = prompter
        self.pretend = pretend
        self.force = force
        self.pinned = pinned  # `--album`: the album to use for the file, instead of searching
        self.steps = frozenset(steps)
        self.progress = progress or (lambda message: None)
        self._analysed: dict = {}  # file -> (FileInfo, md5) read ahead by the matching workers
        self.files_untouched = files_untouched  # `--database-only`: no comic file may be moved, replaced or deleted
        self.retry_skipped = retry_skipped  # ask again about files skipped earlier (`import --skipped`)
        self.phase = phase or (lambda step, title: None)  # ("match" | "fetch", what is about to happen)
        self.bar = bar  # (total, label) -> object with update(detail) and finish(message)
        wanted = settings.import_.auto if auto is None else auto
        self.auto = "all" if wanted == "all" else "strong" if wanted in (True, "strong") else None
        self.workers = resolve_workers(settings.import_.workers if workers is None else workers)
        self._pending: list[tuple[ImportResult, Candidate, str]] = []
        self._early = None  # pages downloaded in the background while files are still being matched (PageQueue)
        self._by_page: dict[str, list] = {}  # album page address -> the pending items waiting for it
        self._by_series: dict[str, list] = {}  # series page address -> the items whose album page is here, waiting for it
        self._done: set[int] = set()  # results (by id) completed early, or undone: the fetch phase skips them
        self._undone: list = []  # the undone results themselves: while they are alive no new result can have their id
        self._completed_early = 0
        self._journal: Optional[Journal] = None  # what the file being matched has recorded so far
        self._stopped = False  # Ctrl-C was pressed during the fetch: nothing more is started afterwards
        self._stream = None  # the background matching of this run (closed by `run` however it ends)
        self._identical_to_trash: list[str] = []  # identical files the person chose to replace: trashed only once the new file is linked
        self._asked = False  # the user was asked about the file being matched
        self.series_mode = settings.import_.series if series is None else series  # ask | always | never
        self._covers: list[ImportResult] = []  # complete albums whose file's cover was never compared
        wanted_check = settings.cover.check if cover_check is None else cover_check
        self.cover_check = covers.opencv_installed() if wanted_check == "auto" else bool(wanted_check)
        self._undecided: list = []  # (path, matches) the strict `--auto=all` left undecided: their covers vote at the end
        self._cover_pool = None  # worker threads that download covers while the matching goes on (CoverPass._queue_cover)
        self._cover_jobs: list = []  # (result, future) of the covers on their way
        self._cover_futures: dict = {}  # (source, album id) -> the download of its cover: files of one album share it
        self._cover_failed = 0  # covers that did not come, this run
        self._ask_covers = True  # False while the matching still runs: a cover that differs is flagged, not asked about
        self._cover_off = False  # set when the check turns out to be impossible (no OpenCV)

    @property
    def recache(self) -> bool:
        """Redoing the fetch means asking the site again, not reading the stored page."""
        if self.refetch is not None:
            return self.refetch and "fetch" in self.steps
        return self.force and "fetch" in self.steps

    # ------------------------------------------------------------------ run
    def run(self, paths: list[str]) -> ImportReport:
        """Import ``paths``. Whatever ends the run (a failure outside a file's own work, Ctrl-C, a stop), the background pieces are closed:
        the page queue, the cover threads (they wait out a polite delay and would keep the program from exiting) and the matching."""
        self._stopped = False
        try:
            return self._run(paths)
        except BaseException:
            stream = getattr(self, "_stream", None)
            if stream is not None:
                stream.close()
            self._drop_covers()
            self._close_early()
            raise

    def _run(self, paths: list[str]) -> ImportReport:
        report = ImportReport()
        files = []
        for path in paths:
            if not os.path.exists(path):  # say so now, before any network access
                report.results.append(ImportResult(path, FAILED, note=f"{path!r} is not an existing file or folder"))
            else:
                files.extend(self._collect([path]))
        if not paths:  # no path: everything the library already knows
            files = [row["path"] for row in self.library.query([], files=True, light=True)]
        if not files:
            return report
        if "match" in self.steps and not self.source.is_ready():
            self.progress(f"Preparing the {self.source.name} source (first run only, may take a minute)...")
            self.source.prepare(progress=self.progress)
        if not self.pretend:
            moved = covers.migrate_flat_covers(self.library, self.settings.cover_path)
            if moved:
                self.progress(f"Moved {moved} cover file(s) into a folder per album (a change of bdnex).")
        self._announce_resume()
        files.sort(key=lambda path: (os.path.dirname(path), path))  # the files of a folder together
        kinds = {path: self._classify(path) for path in files}
        todo = [path for path in files if kinds[path][0] == "match"]
        self._analysed = {}  # file -> (FileInfo, md5) read by the matching workers, or the exception they met
        stream = match_ahead(self.source, todo, workers=self.workers, lookahead=self.settings.import_.lookahead,
                             side=analyse_file, side_results=self._analysed)
        self._stream = stream
        ready: dict[str, object] = {}  # matches read from the stream, waiting for their file
        chosen: dict[str, tuple] = {}  # file -> (album, score, why) decided for a whole folder
        groups: dict[str, list[str]] = {}
        for path in todo:
            groups.setdefault(os.path.dirname(path), []).append(path)
        decided: set[str] = set()
        self._pending = []
        base = len(report.results)  # results of the files before this loop (paths that do not exist)
        history: list[int] = []  # positions of the files the user was asked about, to go back to them
        skipped_folders: set[str] = set()  # folders the user chose to skip
        followed: dict[int, list[str]] = {}  # position -> the files whose album came from the series chosen at that file
        self._series_asked: set[str] = set()  # folders where the user already said what the series is
        position = 0
        self._loop_started = time.monotonic()
        self._open_early()
        if "match" in self.steps:
            self.phase("match", f"Matching {len(files)} file(s) to albums")
        while position < len(files):
            path = files[position]
            kind = kinds[path][0]
            if kind in ("match", "fetch", "cover"):
                if self.verbosity >= 1:
                    self.progress(f"[{position + 1}/{len(files)}] {os.path.basename(path)}")
                else:
                    self._heartbeat(position + 1, len(files))
            self._asked = False
            try:
                matches = None
                if kind == "match" and os.path.dirname(path) in skipped_folders:
                    raise SkipFolder(path)
                if kind == "match":
                    group = groups[os.path.dirname(path)]
                    if len(group) > 1 and self.series_mode != "never" and os.path.dirname(path) not in decided:
                        decided.add(os.path.dirname(path))
                        self._read(stream, ready, group)  # the whole folder's matches, to decide for it
                        chosen.update(self._folder_decision(group, ready))
                    matches = self._read(stream, ready, [path])[path]
                    if isinstance(matches, Exception):
                        raise matches
                result = self.import_file(path, matches=matches, defer_fetch=True, chosen=chosen.get(path))
            except GoBack:
                if not history:
                    self.progress("There is nothing before this file to go back to.")
                    continue  # ask about this file again
                position = history.pop()
                for undone in reversed(report.results[base + position:]):
                    self._undo(undone)
                del report.results[base + position:]
                skipped_folders.discard(os.path.dirname(files[position]))  # asked about again
                chosen.pop(files[position], None)  # a file the folder had decided is asked about on its own
                for later in followed.pop(position, []):  # what was offered for the rest of the folder from this file
                    chosen.pop(later, None)
                self._series_asked.discard(os.path.dirname(files[position]))
                self.progress(f"Back to {os.path.basename(files[position])}: what was decided from there is taken back.")
                continue
            except SkipFolder:
                skipped_folders.add(os.path.dirname(path))
                result = self._skip_file(path, "folder skipped")
            except ImportAborted:
                report.aborted_at, report.total = path, len(files)
                break  # this file stays undecided: it is asked about again next time
            except (NeedsTerminal, KeyboardInterrupt):
                stream.close()  # stop the background matching before leaving
                self._finish_stored()
                self._drop_covers()  # finishing the stored pages queued covers: leaving means leaving, not waiting for them
                raise  # not this file's fault: the decisions made so far are saved, the run stops
            except Exception as err:  # one bad file must not stop the batch
                logger.exception(f"{path} failed")
                result = ImportResult(path, FAILED, note=str(err))
                if not self.pretend:
                    self.library.log_import(path, FAILED)
                    result.journal = Journal(logged=True)
            folder = os.path.dirname(path)
            if kind == "match" and self._asked and folder not in skipped_folders:
                rest = [p for p in files[position + 1:] if kinds[p][0] == "match" and os.path.dirname(p) == folder and p not in chosen]
                offered = self._series_from_choice(path, result, rest, ready, stream) if rest else None
                if offered == "skip":
                    skipped_folders.add(folder)
                elif offered:
                    chosen.update(offered)
                    followed[position] = list(offered)
            report.results.append(result)
            self._attention(result)
            if self._early is not None:  # the pages that arrived meanwhile go in the library (this thread only), and their albums are built
                self._handle_arrivals(self._early.store())
            if self._asked:
                history.append(position)
            position += 1
        stream.close()  # stops the background matching
        if report.aborted_at:
            self._finish_stored()
            self._drop_covers()  # the covers it queued would wait out their polite delays before the program could exit
            self._pending = []  # the pages are fetched by the next run (the albums are "matched")
            self._remember_abort(report, paths)
            return report
        self._fetch_pending()
        if not self._stopped:  # Ctrl-C during the fetch means stop: no cover vote (it downloads covers and links files) afterwards
            self._cover_votes(report)
        report.stopped = self._stopped
        return report

    def _skip_file(self, path: str, note: str) -> ImportResult:
        """Leave a file out without asking (remembered like any other skip; `--force` looks at it again)."""
        self._journal = self._start_journal(path)
        self._log(path, SKIPPED)
        journal, self._journal = self._journal, None
        result = ImportResult(path, SKIPPED, note=note)
        result.journal = journal
        return result

    # ------------------------------------------------------------ aborting and resuming
    ABORT_KEY = "import_aborted"

    def _remember_abort(self, report: ImportReport, paths: list[str]) -> None:
        """Keep, in the library, where the user stopped. The decisions themselves were saved one by one."""
        if self.pretend:
            return
        self.library.set_meta(self.ABORT_KEY, json.dumps({
            "file": report.aborted_at, "done": len(report.results), "total": report.total, "paths": list(paths)}))

    def detail(self, message: str) -> None:
        """A line about one file: shown from ``-v``. At the default level only the files that need a look are reported."""
        if self.verbosity >= 1:
            self.progress(message)

    def _heartbeat(self, number: int, total: int) -> None:
        """The default level's progress: a line every percent or so (at least every 25 files), with the time left."""
        started = self._loop_started
        if number == 1 or number == total or number % max(25, total // 100) == 0:
            elapsed = time.monotonic() - started
            left = f"  about {human_duration(elapsed / number * (total - number))} left" if number >= 25 else ""
            pages = (f"  ·  {self._completed_early} album(s) complete"
                     if self._early is not None and self._completed_early else "")
            self.progress(f"[{number}/{total}] {100 * number // total}%{left}{pages}")

    def _attention(self, result: "ImportResult") -> None:
        """What happened to a file that was not matched, at the default level (``-v`` shows every file, with its reason)."""
        if result.outcome not in (SKIPPED, DUPLICATE, FAILED) or not result.note:
            return
        if self.verbosity >= 1:
            self.progress(f"    {result.outcome}: {result.note}")  # under the file's own line
        else:
            self.progress(f"{result.outcome}: {os.path.basename(result.path)} - {result.note}")

    def _announce_resume(self) -> None:
        """Say that this run continues an aborted one, then forget the record."""
        raw = self.library.get_meta(self.ABORT_KEY)
        if not raw or self.pretend:
            return
        try:
            state = json.loads(raw)
            self.progress(f"Resuming: the last import was aborted at {os.path.basename(state['file'])} "
                          f"({state['done']} of {state['total']} files looked at). Files already decided are not asked again.")
        except (ValueError, KeyError, TypeError):
            pass
        self.library.delete_meta(self.ABORT_KEY)

    # ------------------------------------------------------- folders are series
    @staticmethod
    def _read(stream, ready: dict, wanted: list[str]) -> dict:
        """Take matches from the stream (in file order) until every wanted file has its own."""
        while any(path not in ready for path in wanted):
            path, found = next(stream)
            ready[path] = found
        return ready

    def _folder_decision(self, group: list[str], ready: dict) -> dict[str, tuple]:
        """For a folder with several files: ``{file: (album, score, why)}`` for the ones that are a series.

        A folder whose names make it obvious (every file has its own volume number and they start alike) is
        taken for a series; otherwise the user is asked, unless ``import.series: always``. The series is
        found by letting the files vote (see :mod:`bdnex.importer.folders`). When the user says only some of
        the files belong to it, the ones left over are looked at as the next series, and so on.
        """
        paths = [path for path in group if not isinstance(ready[path], Exception)]
        decided: dict[str, tuple] = {}
        while len(paths) > 1:
            mapping, paths = self._one_series(paths, ready, first=not decided and len(paths) == len(group))
            decided.update(mapping)
        return decided

    def _one_series(self, paths: list[str], ready: dict, first: bool) -> tuple[dict, list[str]]:
        """-> (``{file: (album, score, why)}`` of one series, the files left for another look)."""
        kind = folders.classify(paths)
        if first and kind in (None, folders.VERSIONS):  # versions of one album are the duplicate dialog's business
            return {}, []
        proposal = folders.propose_series(self.source, paths, {path: ready[path] for path in paths})
        if proposal is None:
            return {}, []
        left: list[str] = []
        if self.series_mode == "always" or (first and kind == folders.SERIES and proposal.solid):
            self.progress(f"Series folder {folders.folder_name(paths)}: {len(paths)} files, "
                          f"{proposal.covered} found by volume in \"{proposal.name}\"")
        else:
            self._series_asked.add(os.path.dirname(paths[0]))  # you are asked about this folder once
            answer = self._ask_series(paths, proposal)
            if answer == "skip":
                raise SkipFolder(paths[0])
            if answer == "choose":
                paths, left = self._choose_files(paths, proposal)
                if not paths:
                    return {}, []  # nothing ticked: match every file by itself (and never loop on the same files)
                proposal = folders.propose_series(self.source, paths, {path: ready[path] for path in paths}) \
                    if len(paths) > 1 else None
                if proposal is None:
                    return {}, left
            elif answer != "series":
                return {}, []
        why = f"series \"{proposal.name}\""
        mapping = {path: (candidate, next((m.score for m in ready[path] if m.candidate == candidate), None), why)
                   for path, candidate in proposal.mapping.items()}
        return mapping, left

    def _choose_files(self, paths: list[str], proposal) -> tuple[list[str], list[str]]:
        """The user ticks the files that belong to the series: -> (ticked, the others)."""
        pick = getattr(self.prompter, "pick_files", None)
        ticked = pick(folders.folder_name(paths), paths, folders.likely_members(proposal, paths)) if pick else list(paths)
        ticked = [path for path in paths if path in set(ticked)]
        return ticked, [path for path in paths if path not in set(ticked)]

    def _series_from_choice(self, path: str, result, rest: list[str], ready: dict, stream):
        """You picked an album of a series by hand for one file of a folder whose files were not taken for a series:
        offer that series for the other files, by volume number. ``{file: (album, score, why)}``, ``"skip"`` (skip the
        folder), or ``None`` (nothing to offer, or you said no)."""
        folder = os.path.dirname(path)
        ask = getattr(self.prompter, "confirm_same_series", None)
        if (ask is None or self.pretend or self.auto == "all" or self.series_mode == "never" or folder in self._series_asked
                or result.outcome not in (TAGGED, PARTIAL) or result.album_id is None):
            return None
        series = self.library.series_of_album(result.album_id)
        if series is None:
            return None  # a one-shot: there is no series to offer
        self._read(stream, ready, rest)
        matches = {p: ready[p] for p in rest if not isinstance(ready[p], Exception)}
        proposal = folders.propose_for_series(self.source, series[0], series[1], rest, matches)
        if proposal is None:
            return None
        self._series_asked.add(folder)
        first = os.path.basename(path)
        try:
            answer = ask(folders.folder_name(rest), rest, proposal, first)
        except NeedsTerminal:
            return None
        why = f'series "{series[1]}" (from {first})'
        mapping = {p: (candidate, next((m.score for m in matches.get(p, []) if m.candidate == candidate), None), why)
                   for p, candidate in proposal.mapping.items()}
        if answer == "skip":
            return "skip"
        if answer == "choose":
            ticked = self._choose_files(rest, proposal)[0]
            return {p: m for p, m in mapping.items() if p in ticked}
        return mapping if answer == "series" else None

    def _ask_series(self, paths: list[str], proposal) -> str:
        """``series``, ``each``, ``choose`` or ``skip``. Nobody to ask (preview, ``--auto=all``, no prompter, no terminal): ``each``."""
        ask = getattr(self.prompter, "confirm_series", None)
        if ask is None or self.pretend or self.auto == "all":
            return "each"
        try:
            return ask(folders.folder_name(paths), paths, proposal)
        except NeedsTerminal:
            return "each"

    # ----------------------------------------------------- what to do with a file
    def _classify(self, path: str) -> tuple[str, Optional[dict]]:
        """``match`` (find its album), ``fetch`` (read its album's page), ``already`` (done),
        ``remembered`` (skipped earlier) or ``unmatched`` (nothing to fetch for it yet)."""
        copied = None if self.force else self.library.get_origin(path)
        if copied is not None:  # this file was copied into the collection by an earlier import
            return "copied", copied
        known = self.library.get_file(path)
        album = self.library.get_album(known["album_id"]) if known and known["album_id"] is not None else None
        if album is not None:
            if "match" in self.steps and (self.force or not self.settings.import_.resume):
                return "match", album
            partial = bool(album["extra"].get("partial"))
            mine = album["source"] == self.source.name
            if "fetch" in self.steps and mine and (partial or self.force):
                return "fetch", album
            if "fetch" in self.steps and mine and album["cover_url"] and (
                    (self.cover_check and known["cover_score"] is None) or self._cover_missing(album)):
                return "cover", album  # complete, but its cover was never saved, or compared with the file's
            return "already", album
        if "match" not in self.steps:
            return "unmatched", None
        if not self.force and self._skipped_before(path):
            return "remembered", None
        return "match", None

    def _skipped_before(self, path: str) -> bool:
        """You (or the matching) skipped this file, and no new albums have been downloaded since."""
        last = self.library.last_import(path)
        if self.retry_skipped or not last or last["outcome"] != SKIPPED:
            return False
        refreshed = self.library.get_meta("sitemaps_at")
        return not (refreshed and refreshed > last["at"])

    def _without_matching(self, path: str, kind: str, album: Optional[dict], defer_fetch: bool) -> ImportResult:
        if kind == "copied":
            note = f"already copied to {album['path']}"
            try:
                stat = os.stat(path)
                if stat.st_size != album["size"] or abs(stat.st_mtime - (album["mtime"] or 0)) > 2:
                    note += " (it changed at the source since: left alone; `--force` imports it again)"
            except OSError:
                pass
            return ImportResult(path, ALREADY, note=note)
        if kind == "remembered":
            return ImportResult(path, SKIPPED, note="skipped earlier")
        if kind == "unmatched":
            return ImportResult(path, SKIPPED, note="not matched yet: `bdnex import --match` first")
        if kind == "already":
            more = (" (its details are not fetched yet: `bdnex import --fetch`)"
                    if album["extra"].get("partial") and "fetch" not in self.steps else "")
            return ImportResult(path, ALREADY, album_id=album["id"],
                                note=f"already imported as {album_label(album)}{more}; `--force` redoes it")
        if kind == "cover":
            result = ImportResult(path, ALREADY, album_id=album["id"], note=f"already imported as {album_label(album)}")
            if not self.pretend:
                self._queue_cover(result)  # complete albums that lack a cover: downloaded in the background too
                if not defer_fetch:
                    self._fetch_pending()
            return result
        # fetch: the album is known, its page is not (or is to be read again)
        candidate = Candidate(album["source_id"], album["url"], "", album["volume"])
        if self.pretend:
            return ImportResult(path, PRETEND, album_id=album["id"], note=f"would fetch {album['url']}")
        result = ImportResult(path, PARTIAL, album_id=album["id"], note=f"matched as {album_label(album)}")
        self._pending.append((result, candidate, ""))
        self._queue_page(self._pending[-1])
        if not defer_fetch:
            self._fetch_pending()
        return result










    def _fetch_pending(self) -> None:
        """Read the album pages of the files linked so far: one polite request each."""
        if "fetch" not in self.steps:
            self._pending = []
            return
        try:
            self._drain_early()  # the albums whose pages were asked for during the matching complete as they arrive
        except KeyboardInterrupt:
            self._stopped = True
            self._drop_covers()
            self._pending = []
            self._close_early()
            self.progress("Interrupted: the albums already complete are saved; `bdnex import --fetch` reads the rest "
                          "of the pages and downloads the covers that are missing.")
            return
        pending = [item for item in self._pending if id(item[0]) not in self._done]
        self._pending = []
        if not pending:
            self._close_early()
            self._check_covers()
            return
        self.phase("fetch", f"Reading {len(pending)} album page(s): authors, publisher, summary... "
                            "(one polite request each, Ctrl-C stops and `bdnex import --fetch` resumes)")
        bar = self.bar(len(pending), "Album pages") if self.bar else None
        if bar is None:
            self.progress(f"Fetching {len(pending)} album page(s) (Ctrl-C stops; `bdnex import --fetch` resumes)...")
        workers = getattr(self.source, "parallel", 1)
        arriving = None
        current = [""]  # the page the bar is on

        def busy(name: str) -> str:
            """What the bar says: with several pages at once, the titles being fetched and how many are done."""
            if arriving is None:
                return name
            titles = getattr(self.source, "fetching_now", lambda: [])()
            done = getattr(self.source, "downloaded", 0)
            return (" · ".join(titles) if titles else name) + f"  ({done} of {len(pending)} fetched)"

        if self._early is not None:
            if self._completed_early:
                self.progress(f"{self._completed_early} album(s) were completed while matching; "
                              f"{len(pending)} left (fetching {workers} at a time).")
            arriving = self._early.arrivals([candidate.url for _, candidate, _ in pending],
                                            tick=lambda: bar and bar.refresh(busy(current[0])))
        elif workers > 1 and hasattr(self.source, "prefetch") and not self.pretend:
            self.progress(f"Fetching {workers} pages at a time.")
            arriving = self.source.prefetch([candidate.url for _, candidate, _ in pending], workers, recache=self.recache,
                                            tick=lambda: bar and bar.refresh(busy(current[0])))
        try:
            for number, (result, candidate, damaged) in enumerate(pending, start=1):
                name = candidate.url.rsplit("/", 1)[-1]
                current[0] = name
                if bar:
                    bar.update(busy(name))
                else:
                    self.progress(f"[page {number}/{len(pending)}] {name}")
                try:
                    if arriving is not None:
                        _, failure = next(arriving)  # the page is in the library now (or why it is not)
                        if failure is not None:
                            raise failure
                        self._fresh.add(candidate.url)  # downloaded just now: not asked for a second time
                    album_id, partial, note = self._album_for(candidate)
                except Exception as err:
                    logger.exception(f"{candidate.url} failed")
                    result.note = f"page not fetched: {err}"
                    continue
                result.album_id = album_id
                result.outcome = PARTIAL if partial else TAGGED
                result.note = "; ".join(n for n in (note, damaged) if n)
                self._log(result.path, result.outcome, result.score, album_id)
                if not partial:
                    self._confirm(result)
                if not partial and (self.cover_check or self.settings.cover.download):
                    self._check_cover(result)
        except KeyboardInterrupt:
            self._stopped = True
            self._drop_covers()  # stop means stop: no cover pass afterwards (the next `--fetch` gets them)
            self.progress("Interrupted: the files stay linked; `bdnex import --fetch` reads the rest of the pages "
                          "and downloads the covers that are missing.")
            return
        finally:
            if arriving is not None and hasattr(arriving, "close"):
                arriving.close()  # stops the pages still being fetched
            self._close_early()
            if bar:
                bar.finish()
        self._check_covers()







    @staticmethod
    def _collect(paths: list[str]) -> list[str]:
        files: list[str] = []
        for path in paths:
            if os.path.isdir(path):
                files.extend(iter_comic_files(path))
            else:
                files.append(os.path.abspath(path))
        return files

    # ------------------------------------------------------------ one file
    def import_file(self, path: str, matches: Optional[list[Match]] = None,
                    defer_fetch: bool = False, chosen: Optional[tuple] = None) -> ImportResult:
        """Import one file. ``matches`` come from ``pipeline.match_ahead`` (computed here when absent);
        with ``defer_fetch`` the album page is left for :meth:`_fetch_pending`."""
        kind, album = self._classify(path)
        if kind != "match":
            return self._without_matching(path, kind, album, defer_fetch)
        self._journal = self._start_journal(path)
        try:
            result = self._import_matched(path, matches, defer_fetch, chosen)
        except BaseException as err:  # a question left unanswered, a failure, Ctrl-C: leave nothing half done behind
            try:
                self._undo_journal(self._journal, path)
            except Exception:
                logger.exception(f"{path}: what was recorded for it could not be taken back")
            if not isinstance(err, (GoBack, ImportAborted, SkipFolder)):
                logger.info(f"{path}: {err!r}: its link and album were taken back")
            raise
        finally:
            journal, self._journal = self._journal, None
        result.journal = journal
        return result

    def _import_matched(self, path: str, matches: Optional[list[Match]], defer_fetch: bool,
                        chosen: Optional[tuple]) -> ImportResult:
        self._identical_to_trash = []  # what the file before it wanted replaced is not this file's
        info, md5 = self._analysis(path)
        logger.debug("%s: %s, %s page(s), %s", os.path.basename(path), info.format, info.page_count,
                     "read ahead" if path in self._analysed else "read here")
        if not info.readable:
            self._log(path, FAILED)
            return ImportResult(path, FAILED, note=info.problem or "unreadable archive")
        damaged = f"archive damaged ({info.problem}): `bdnex repair` can try to fix it" if info.corrupted else ""

        dup = self._duplicate_file(path, md5)
        if dup is not None:
            logger.debug("%s: identical file already registered (%s)", os.path.basename(path), dup.note)
            return dup

        matches = matches if matches is not None else self._matches(path)
        if self.pinned is not None:
            matches = [Match(self.pinned, 100.0, 100.0)]
        via = ""
        if chosen is not None:  # decided for the whole folder
            candidate, score, via = chosen
        else:
            candidate, score = self._select(path, matches)
        if candidate is None:
            self._log(path, SKIPPED, matches[0].score if matches else None)  # how close the best guess was
            return ImportResult(path, SKIPPED, note=self._why_no_match(matches))

        shown = via or (f"{score:.0f}%" if score is not None else "chosen by you")
        self.detail(f"    match: {candidate.url.rsplit('/', 1)[-1]} ({shown})")
        if self.pretend:
            return ImportResult(path, PRETEND, score=score,
                                note=f"would link to {candidate.url}")

        album_action, others = self._duplicate_album(path, candidate)
        if album_action == "skip":
            self._log(path, DUPLICATE, score)
            return ImportResult(path, DUPLICATE, score=score, note="album already in library")

        existed = self.library.find_album(self.source.name, candidate.source_id) is not None
        album_id, partial, note = self._album_for(candidate, fetch=not defer_fetch and 'fetch' in self.steps)
        if not existed and not self.pretend:
            self._journal.created_album = album_id
        note = "; ".join(n for n in (note, damaged) if n)
        replaced = list(dict.fromkeys([*(others if album_action == "replace" else []), *self._identical_to_trash]))
        self._identical_to_trash = []
        if replaced:
            self._trash_others(replaced, path)
            note = "; ".join(n for n in (note, f"{len(replaced)} replaced file(s) are in the trash (`bdnex trash`)") if n)
        before = self.library.get_file(path)
        self._journal.new_file = before is None
        if before is not None:  # imported again (--force, --album): taking it back means going back to what it was linked to
            self._journal.previous_album, self._journal.previous_review = before["album_id"], before.get("review")
            self._journal.previous_spare = int(bool(before.get("spare")))
        self.library.add_analysed(info, md5, album_id)
        self._journal.linked = True
        if album_action == "keep_both" and any(
                f["path"] != os.path.abspath(path) and not f["spare"] for f in self.library.files_for_album(album_id)):
            self.library.set_file_fields(path, spare=1)  # another file is the main one: this is a spare version
            note = "; ".join(n for n in (note, "kept as a spare version") if n)
        elif before is not None and before.get("spare"):
            self.library.set_file_fields(path, spare=0)  # linked again to an album where it is the only file: not a spare of anything
        outcome = PARTIAL if partial else TAGGED
        self._log(path, outcome, score, album_id)
        if partial and "fetch" not in self.steps:
            note = "; ".join(n for n in (note, "details not fetched: `bdnex import --fetch` gets them") if n)
        result = ImportResult(path, outcome, score=score, album_id=album_id, note=note)
        if not partial:
            self._confirm(result)  # (offline: the album is complete, in the library already or just fetched)
        if not partial and not defer_fetch and (self.cover_check or self.settings.cover.download):
            self._check_cover(result)  # the page was read just now: keep (and compare) the cover too
        if defer_fetch and partial and "fetch" in self.steps:
            self._pending.append((result, candidate, damaged))
            self._queue_page(self._pending[-1])
        return result

    # -------------------------------------------------------------- helpers
    def _why_no_match(self, matches) -> str:
        """Why a file was left unmatched: the best guess and how far it was from being taken."""
        if not matches:
            return "no match: nothing in the album list looks like this name"
        best = matches[0]
        strong, medium = self.settings.match.strong_threshold, self.settings.match.medium_threshold
        title = best.candidate.url.rsplit("/", 1)[-1]
        if self._asked:
            return f"you chose none of the candidates (best guess {title}, {best.score:.0f})"
        if best.score >= medium:
            why = f"sure from {strong:.0f}" if not best.doubt else best.doubt
            return f"best guess {title} scored {best.score:.0f}: not applied without asking ({why}, or --auto=all)"
        return f"no match: best guess {title} scored {best.score:.0f}, under {medium:.0f}, the lowest score applied"

    def _log(self, path, outcome, score=None, album_id=None):
        if not self.pretend:
            self.library.log_import(path, outcome, score, album_id)
            if self._journal is not None:
                self._journal.logged = True

    def _undo(self, result: ImportResult) -> None:
        """Take back what matching this file recorded (nothing for files that were only fetched or skipped over)."""
        self._pending = [item for item in self._pending if item[0] is not result]
        self._covers = [item for item in self._covers if item is not result]  # its cover check would run against the album chosen next
        self._cover_jobs = [job for job in self._cover_jobs if job[0] is not result]
        self._undone.append(result)  # kept: a freed result's id could be reused by the next one, which `_done` would then skip
        self._done.add(id(result))  # a page still on its way must not complete the album for a file taken back
        self._undo_journal(result.journal, result.path)

    def _start_journal(self, path: str) -> Journal:
        """A journal for one file's import, that knows where the file's import log stood before."""
        return Journal(log_floor=None if self.pretend else self.library.last_import_id(path))

    def _undo_journal(self, journal: Optional[Journal], path: str) -> None:
        if journal is None or self.pretend:
            return
        if journal.linked:
            if journal.new_file:
                self.library.remove_file(path)
            else:
                self.library.set_file_album(path, journal.previous_album)  # what it was linked to before, not nothing
                self.library.set_file_fields(path, spare=journal.previous_spare)  # and as a spare version, or not, as it was
                if journal.previous_review:
                    self.library.flag_review(path, journal.previous_review)
        if journal.logged:
            if journal.log_floor is None:
                self.library.forget_last_import(path)
            else:  # an import writes a row when it matches and another when the page arrives: both go
                self.library.forget_imports_since(path, journal.log_floor)
        if journal.created_album is not None and self.library.delete_album_if_unused(journal.created_album):
            self.library.prune_orphan_series()
        for entry in reversed(journal.trashed):  # files this one replaced come back
            trash.restore(self.library, entry)

    def _resolve_duplicate(self, path, others, what) -> str:
        """``skip`` (keep the existing files), ``keep_both`` or ``replace`` (the existing files go to the trash)."""
        if self.duplicates == "keep_both":
            return "keep_both"  # only the library changes (the file becomes a spare version): safe with `--database-only`
        if self.files_untouched:
            return "skip"  # `replace` would put a file in the trash, `keep_both` may move one: neither is allowed
        action = self.settings.duplicates.action
        if action != "ask":
            return action
        if self.auto == "all" or self.prompter is None or self.pretend:
            return "skip"  # never silently create/replace duplicates; a preview does not ask
        chooser = getattr(self.prompter, "resolve_duplicate", None)
        if chooser is None:
            return self.prompter.duplicate_action(path, others, what)
        existing = [facts_of(self.library, other) for other in others]
        comparison = compare(existing, facts_of(self.library, path), self.settings.duplicates.prefer)
        return chooser(comparison, what, self.library.root)

    def _trash_others(self, others, new_path) -> None:
        for other in others:
            source = self.settings.source_for(other)
            if source is not None and source.mode == "copy":  # a source that is only ever copied is never changed: the file stays
                self.detail(f"    {os.path.basename(other)} is in the read-only source {source.alias!r}: it is not replaced")
                continue
            if os.path.exists(other):
                entry = trash.move_to_trash(self.library, self.settings, other, f"replaced by {new_path}")
                if self._journal is not None:
                    self._journal.trashed.append(entry)
            else:
                self.library.remove_file(other)  # already gone from the disk: only the record is left

    def _analysis(self, path: str):
        """``(FileInfo, md5)`` of a file. The matching workers read it ahead (the whole file is read once, in parallel);
        when they did not (a single file, a failure), it is read here."""
        found = self._analysed.get(path)  # kept: going Back to a file must not read it again
        if found is None or isinstance(found, Exception):
            return analyse_file(path)
        return found

    def _duplicate_file(self, path, md5: Optional[str] = None) -> Optional[ImportResult]:
        """Same bytes already registered under another path."""
        same = [f["path"] for f in self.library.files_with_md5(md5 or file_md5(path))
                if f["path"] != os.path.abspath(path) and os.path.exists(f["path"])]  # a file moved or deleted outside bdnex is no duplicate
        if not same:
            return None
        action = self._resolve_duplicate(path, same, "identical file")
        if action == "keep_both":
            return None
        if action == "replace":
            if not self.pretend:  # a preview trashes nothing, and nothing is trashed for a file that ends up unlinked or skipped:
                self._identical_to_trash = same  # it is done when the new file is linked (`_import_matched`)
            return None
        self._log(path, DUPLICATE)
        return ImportResult(path, DUPLICATE, note=f"identical to {same[0]}")

    def _duplicate_album(self, path, candidate: Candidate) -> tuple[str, list[str]]:
        """Another file for the same album, or for an album with the same `duplicates.keys`
        (e.g. two editions of the same tome). Returns (skip | keep_both | replace | none, those files)."""
        info = self.source.partial_info(candidate)
        available = {"series": info.series.name if info.series else None, "volume": info.volume,
                     "title": info.title}
        keys = {k: available[k] for k in self.settings.duplicates.keys if available.get(k) is not None}
        # a one-shot has no series/volume: only an identical album counts, never "all one-shots"
        if not info.series:
            keys = {k: v for k, v in keys.items() if k == "title"}
        others = [f["path"] for f in self.library.files_matching_album(
            self.source.name, candidate.source_id, keys, path)
            if f["path"] not in self._identical_to_trash and os.path.exists(f["path"])]  # already chosen to be replaced; gone from the disk
        if not others:
            return "none", []
        return self._resolve_duplicate(path, others, "album already linked"), others

    def _matches(self, path) -> list[Match]:
        return find_matches(self.source, path)

    def _select(self, path, matches: list[Match]) -> tuple[Optional[Candidate], Optional[float]]:
        strong, medium = self.settings.match.strong_threshold, self.settings.match.medium_threshold
        best = matches[0] if matches else None
        if self.pinned is not None and best is not None:
            return best.candidate, 100.0  # you named the album: nothing to ask
        rec = recommend(best.score, strong, medium) if best else Recommendation.NONE
        if rec is Recommendation.STRONG and best.doubt:
            rec = Recommendation.MEDIUM  # sure by its score, but something says to look: ask, or (--auto=all) apply as a medium match
        never_ask = self.auto == "all" or self.pretend  # a preview must not stop at every file
        if rec is Recommendation.STRONG and (self.auto or never_ask or self.prompter is None):
            return best.candidate, best.score  # otherwise the user confirms it, pre-selected
        if never_ask:
            # `--auto=all` never asks: a medium match is applied only when `match.auto_all` says so (a preview shows both)
            if rec is Recommendation.MEDIUM and (self.settings.match.auto_all == "medium" or self.auto != "all"):
                return best.candidate, best.score
            if rec is Recommendation.MEDIUM:
                self._queue_vote(path, matches)  # not applied, not lost: its cover gets a vote at the end of the run
            return None, None
        if self.prompter is None:
            return None, None
        self._asked = True
        chosen = self.prompter.choose_match(path, matches)
        if chosen is None:
            return None, None
        score = next((m.score for m in matches if m.candidate == chosen), None)
        return chosen, score

    def _album_for(self, candidate: Candidate, fetch: bool = True) -> tuple[int, bool, str]:
        """Return (album_id, partial, note), storing the album when it is new or refreshed.

        With ``fetch=False`` no page is requested: an unknown album is stored as known from the
        sitemap (partial) and the caller completes it later.
        """
        existing = self.library.find_album(self.source.name, candidate.source_id)
        existing_full = (existing is not None
                         and not self.library.get_album(existing)["extra"].get("partial"))
        if existing_full and not self.recache:
            self.detail("    album already complete in the library: nothing to fetch")
            return existing, False, "album already in library"
        if not fetch:
            if existing is not None:
                return existing, True, ""
            return self.library.add_album(self.source.partial_info(candidate)), True, ""
        self.detail(f"    fetching the album page ({self.source.name}; the first request waits a few seconds)...")
        started = time.monotonic()
        try:
            extra = {"album_fresh": True} if self.recache and candidate.url in self._fresh else {}
            info = self.source.album_info(candidate.url, recache=self.recache, **extra)
            partial, note = False, ""
            if (info.extra or {}).get("series_page_missing"):  # kept, but without its summary and its series' albums: not complete
                partial, note = True, "the series page could not be read: `bdnex import --fetch` tries again"
            self.detail(f"    page read in {time.monotonic() - started:.1f}s")
        except self.source.fetch_errors as err:
            self.progress(f"    page unavailable after {time.monotonic() - started:.1f}s: {err}")
            logger.warning(f"Page for {candidate.url} unavailable ({err}); using sitemap data only")
            if existing_full:
                return existing, False, "kept earlier metadata"
            info = self.source.partial_info(candidate)
            partial, note = True, f"partial: save the page {candidate.url} to import full metadata"
        before = self.library.series_id_of(existing)
        album_id = self.library.add_album(info)
        if before is not None and before != self.library.series_id_of(album_id):
            self.library.delete_series_if_unused(before)  # the placeholder series a partial album had, replaced by the real one
        return album_id, partial, note
