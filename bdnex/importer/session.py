"""The import session: match files to albums and record the result in the library.

Pipeline: file names are matched in the background (``pipeline.match_ahead``) while the user
reviews earlier files. Per file: skip if already imported -> corruption/duplicate-file checks ->
candidates -> score -> (ask) -> link the file to the album as known from the sitemap alone.
Afterwards the album pages are fetched, politely and slowly, and complete those albums; an
interrupted fetch loses nothing (``import --fetch`` resumes).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from bdnex import trash
from bdnex.config import Settings
from bdnex.importer import covers
from bdnex.importer.matching import Match, Recommendation, find_matches, recommend
from bdnex.importer.pipeline import match_ahead, resolve_workers
from bdnex.importer.source import Candidate, MetadataSource
from bdnex.library import Library
from bdnex.library.hashing import file_md5
from bdnex.library.scan import inspect_file, iter_comic_files
from bdnex.versions import compare, facts_of

logger = logging.getLogger(__name__)

TAGGED, PARTIAL, SKIPPED, DUPLICATE, FAILED, PRETEND, ALREADY = (
    "tagged", "partial", "skipped", "duplicate", "failed", "pretend", "already")
# What a user reads for the outcomes that were named after the database (stored values do not change).
OUTCOME_LABELS = {TAGGED: "complete", PARTIAL: "matched"}
STEPS = ("match", "fetch")


class ImportAborted(RuntimeError):
    """The user chose to stop the import: what was decided so far stays, nothing else is started."""


class NeedsTerminal(RuntimeError):
    """A question has to be asked but there is nobody to ask: the import stops instead of failing file by file."""


class Prompter(Protocol):
    def choose_match(self, path: str, matches: list[Match]) -> Optional[Candidate]:
        """Pick a candidate (or None to skip). ``matches`` may be empty."""

    def duplicate_action(self, path: str, others: list[str], what: str) -> str:
        """Return ``skip``, ``keep_both`` or ``replace``."""


@dataclass
class ImportResult:
    path: str
    outcome: str
    score: Optional[float] = None
    album_id: Optional[int] = None
    note: str = ""


@dataclass
class ImportReport:
    results: list[ImportResult] = field(default_factory=list)
    aborted_at: Optional[str] = None  # the file being decided when the user aborted
    total: int = 0  # files the run had to look at (only set when it was aborted)

    def count(self, outcome: str) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)


def album_label(album: dict) -> str:
    """``Nains - T01 - Redwin de la Forge`` for messages."""
    title, series, volume = album.get("title") or "", album.get("series"), album.get("volume")
    if series and volume is not None:
        return f"{series} - T{int(volume):02d}" + (f" - {title}" if title and title != series else "")
    return title or series or "?"


class ImportSession:
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
                 cover_check: Optional[bool] = None):
        self.library = library
        self.source = source
        self.settings = settings
        self.prompter = prompter
        self.pretend = pretend
        self.force = force
        self.steps = frozenset(steps)
        self.progress = progress or (lambda message: None)
        self.bar = bar  # (total, label) -> object with update(detail) and finish(message)
        wanted = settings.import_.auto if auto is None else auto
        self.auto = "all" if wanted == "all" else "strong" if wanted in (True, "strong") else None
        self.workers = resolve_workers(settings.import_.workers if workers is None else workers)
        self._pending: list[tuple[ImportResult, Candidate, str]] = []
        self._covers: list[ImportResult] = []  # complete albums whose file's cover was never compared
        self.cover_check = settings.cover.check if cover_check is None else cover_check
        self._cover_off = False  # set when the check turns out to be impossible (no OpenCV)

    @property
    def recache(self) -> bool:
        """Redoing the fetch means asking the site again, not reading the stored page."""
        return self.force and "fetch" in self.steps

    # ------------------------------------------------------------------ run
    def run(self, paths: list[str]) -> ImportReport:
        report = ImportReport()
        files = []
        for path in paths:
            if not os.path.exists(path):  # say so now, before any network access
                report.results.append(ImportResult(path, FAILED, note=f"{path!r} is not an existing file or folder"))
            else:
                files.extend(self._collect([path]))
        if not paths:  # no path: everything the library already knows
            files = [row["path"] for row in self.library.query([], files=True)]
        if not files:
            return report
        if "match" in self.steps and not self.source.is_ready():
            self.progress(f"Preparing the {self.source.name} source (first run only, may take a minute)...")
            self.source.prepare(progress=self.progress)
        self._announce_resume()
        kinds = {path: self._classify(path) for path in files}
        todo = [path for path in files if kinds[path][0] == "match"]
        stream = match_ahead(self.source, todo, workers=self.workers,
                             lookahead=self.settings.import_.lookahead)
        self._pending = []
        for number, path in enumerate(files, start=1):
            kind = kinds[path][0]
            if kind in ("match", "fetch", "cover"):
                self.progress(f"[{number}/{len(files)}] {os.path.basename(path)}")
            try:
                matches = None
                if kind == "match":
                    _, matches = next(stream)
                    if isinstance(matches, Exception):
                        raise matches
                result = self.import_file(path, matches=matches, defer_fetch=True)
            except ImportAborted:
                report.aborted_at, report.total = path, len(files)
                break  # this file stays undecided: it is asked about again next time
            except NeedsTerminal:
                raise  # not this file's fault: the decisions made so far are saved, the run stops
            except Exception as err:  # one bad file must not stop the batch
                logger.exception(f"{path} failed")
                result = ImportResult(path, FAILED, note=str(err))
                if not self.pretend:
                    self.library.log_import(path, FAILED)
            report.results.append(result)
        stream.close()  # stops the background matching
        if report.aborted_at:
            self._pending = []  # the pages are fetched by the next run (the albums are "matched")
            self._remember_abort(report, paths)
            return report
        self._fetch_pending()
        return report

    # ------------------------------------------------------------ aborting and resuming
    ABORT_KEY = "import_aborted"

    def _remember_abort(self, report: ImportReport, paths: list[str]) -> None:
        """Keep, in the library, where the user stopped. The decisions themselves were saved one by one."""
        if self.pretend:
            return
        self.library.set_meta(self.ABORT_KEY, json.dumps({
            "file": report.aborted_at, "done": len(report.results), "total": report.total, "paths": list(paths)}))

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

    # ----------------------------------------------------- what to do with a file
    def _classify(self, path: str) -> tuple[str, Optional[dict]]:
        """``match`` (find its album), ``fetch`` (read its album's page), ``already`` (done),
        ``remembered`` (skipped earlier) or ``unmatched`` (nothing to fetch for it yet)."""
        known = self.library.get_file(path)
        album = self.library.get_album(known["album_id"]) if known and known["album_id"] is not None else None
        if album is not None:
            if "match" in self.steps and (self.force or not self.settings.import_.resume):
                return "match", album
            partial = bool(album["extra"].get("partial"))
            mine = album["source"] == self.source.name
            if "fetch" in self.steps and mine and (partial or self.force):
                return "fetch", album
            if "fetch" in self.steps and mine and self.cover_check and known["cover_score"] is None and album["cover_url"]:
                return "cover", album  # complete, but its cover was never compared with the file's
            return "already", album
        if "match" not in self.steps:
            return "unmatched", None
        if not self.force and self._skipped_before(path):
            return "remembered", None
        return "match", None

    def _skipped_before(self, path: str) -> bool:
        """You (or the matching) skipped this file, and no new albums have been downloaded since."""
        last = self.library.last_import(path)
        if not last or last["outcome"] != SKIPPED:
            return False
        refreshed = self.library.get_meta("sitemaps_at")
        return not (refreshed and refreshed > last["at"])

    def _without_matching(self, path: str, kind: str, album: Optional[dict], defer_fetch: bool) -> ImportResult:
        if kind == "remembered":
            return ImportResult(path, SKIPPED, note="skipped earlier; `--force` looks at it again "
                                                    "(so does `bdnex init` when it finds new albums)")
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
                self._covers.append(result)
                if not defer_fetch:
                    self._fetch_pending()
            return result
        # fetch: the album is known, its page is not (or is to be read again)
        candidate = Candidate(album["source_id"], album["url"], "", album["volume"])
        if self.pretend:
            return ImportResult(path, PRETEND, album_id=album["id"], note=f"would fetch {album['url']}")
        result = ImportResult(path, PARTIAL, album_id=album["id"], note=f"matched as {album_label(album)}")
        self._pending.append((result, candidate, ""))
        if not defer_fetch:
            self._fetch_pending()
        return result

    def _fetch_pending(self) -> None:
        """Read the album pages of the files linked so far: one polite request each."""
        pending, self._pending = self._pending, []
        if "fetch" not in self.steps:
            return
        if not pending:
            self._check_covers()
            return
        bar = self.bar(len(pending), "Album pages") if self.bar else None
        if bar is None:
            self.progress(f"Fetching {len(pending)} album page(s) (Ctrl-C stops; `bdnex import --fetch` resumes)...")
        try:
            for number, (result, candidate, damaged) in enumerate(pending, start=1):
                name = candidate.url.rsplit("/", 1)[-1]
                if bar:
                    bar.update(name)
                else:
                    self.progress(f"[page {number}/{len(pending)}] {name}")
                try:
                    album_id, partial, note = self._album_for(candidate)
                except Exception as err:
                    logger.exception(f"{candidate.url} failed")
                    result.note = f"page not fetched: {err}"
                    continue
                result.album_id = album_id
                result.outcome = PARTIAL if partial else TAGGED
                result.note = "; ".join(n for n in (note, damaged) if n)
                self._log(result.path, result.outcome, result.score, album_id)
                if not partial and self.cover_check:
                    self._check_cover(result)
        except KeyboardInterrupt:
            self.progress("Interrupted: the files stay linked; `bdnex import --fetch` reads the rest of the pages.")
        finally:
            if bar:
                bar.finish()
        self._check_covers()

    # ------------------------------------------------------------- covers
    def _check_covers(self) -> None:
        """Compare the covers of complete albums that were not part of this run's fetching."""
        waiting, self._covers = self._covers, []
        if waiting and not self._cover_off:
            self.progress(f"Comparing {len(waiting)} cover(s) (one request each, politely)...")
        for result in waiting:
            self._check_cover(result)

    def _check_cover(self, result: ImportResult) -> None:
        """Compare the file's cover with the album's: below ``cover.match_percentage`` it is probably
        the wrong album. The score is kept with the file (``ls --files cover:..40``)."""
        path = result.path
        if self._cover_off or result.album_id is None:
            return
        fetch_image = getattr(self.source, "cover_image", None)
        album, row = self.library.get_album(result.album_id), self.library.get_file(path)
        if fetch_image is None or not album or not album["cover_url"] or row is None:
            return
        try:
            local = covers.front_cover(path, row["format"])
            online = fetch_image(album["cover_url"], recache=self.recache)
            if not local or not online:
                return
            score = round(covers.sift_similarity(local, online), 1)
        except covers.CoverUnavailable as err:
            self.progress(f"cover.check is on but {err}")
            self._cover_off = True
            return
        except (covers.CoverError, *self.source.fetch_errors) as err:
            result.note = "; ".join(n for n in (result.note, f"cover not compared: {err}") if n)
            return
        self.library.set_file_fields(path, cover_score=score)
        threshold = self.settings.cover.match_percentage
        if score >= threshold:
            return
        choice = "keep"
        asking = self.prompter is not None and self.auto != "all" and not self.pretend
        if asking and hasattr(self.prompter, "cover_mismatch"):
            try:
                choice = self.prompter.cover_mismatch(path, album_label(album), score, threshold)
            except NeedsTerminal:
                choice = "keep"
        if choice == "unlink":
            self.library.set_file_album(path, None)
            self.library.log_import(path, SKIPPED, score)
            result.outcome, result.album_id = SKIPPED, None
            result.note = f"cover differs ({score:.0f}% < {threshold:.0f}%): unlinked from {album_label(album)}"
        else:
            result.note = "; ".join(n for n in (result.note, f"the cover differs from the album's ({score:.0f}% < {threshold:.0f}%): "
                                                f"check `bdnex ls --files cover:..{threshold:.0f}`") if n)

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
                    defer_fetch: bool = False) -> ImportResult:
        """Import one file. ``matches`` come from ``pipeline.match_ahead`` (computed here when absent);
        with ``defer_fetch`` the album page is left for :meth:`_fetch_pending`."""
        kind, album = self._classify(path)
        if kind != "match":
            return self._without_matching(path, kind, album, defer_fetch)

        info = inspect_file(path)
        if not info.readable:
            self._log(path, FAILED)
            return ImportResult(path, FAILED, note=info.problem or "unreadable archive")
        damaged = f"archive damaged ({info.problem}): `bdnex repair` can try to fix it" if info.corrupted else ""

        dup = self._duplicate_file(path)
        if dup is not None:
            return dup

        matches = matches if matches is not None else self._matches(path)
        candidate, score = self._select(path, matches)
        if candidate is None:
            self._log(path, SKIPPED, matches[0].score if matches else None)  # how close the best guess was
            return ImportResult(path, SKIPPED, note="no match")

        shown = f"{score:.0f}" if score is not None else "chosen by you"
        self.progress(f"    match: {candidate.url.rsplit('/', 1)[-1]} (score {shown})")
        if self.pretend:
            return ImportResult(path, PRETEND, score=score,
                                note=f"would link to {candidate.url}")

        album_action, others = self._duplicate_album(path, candidate)
        if album_action == "skip":
            self._log(path, DUPLICATE, score)
            return ImportResult(path, DUPLICATE, score=score, note="album already in library")

        album_id, partial, note = self._album_for(candidate, fetch=not defer_fetch and 'fetch' in self.steps)
        note = "; ".join(n for n in (note, damaged) if n)
        if album_action == "replace" and others:
            self._trash_others(others, path)
            note = "; ".join(n for n in (note, f"{len(others)} replaced file(s) are in the trash (`bdnex trash`)") if n)
        self.library.add_file(path, album_id)
        if album_action == "keep_both" and any(
                f["path"] != os.path.abspath(path) and not f["spare"] for f in self.library.files_for_album(album_id)):
            self.library.set_file_fields(path, spare=1)  # another file is the main one: this is a spare version
            note = "; ".join(n for n in (note, "kept as a spare version") if n)
        outcome = PARTIAL if partial else TAGGED
        self._log(path, outcome, score, album_id)
        if partial and "fetch" not in self.steps:
            note = "; ".join(n for n in (note, "details not fetched: `bdnex import --fetch` gets them") if n)
        result = ImportResult(path, outcome, score=score, album_id=album_id, note=note)
        if defer_fetch and partial and "fetch" in self.steps:
            self._pending.append((result, candidate, damaged))
        return result

    # -------------------------------------------------------------- helpers
    def _log(self, path, outcome, score=None, album_id=None):
        if not self.pretend:
            self.library.log_import(path, outcome, score, album_id)

    def _resolve_duplicate(self, path, others, what) -> str:
        """``skip`` (keep the existing files), ``keep_both`` or ``replace`` (the existing files go to the trash)."""
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
            if os.path.exists(other):
                trash.move_to_trash(self.library, self.settings, other, f"replaced by {new_path}")
            else:
                self.library.remove_file(other)  # already gone from the disk: only the record is left

    def _duplicate_file(self, path) -> Optional[ImportResult]:
        """Same bytes already registered under another path."""
        same = [f["path"] for f in self.library.files_with_md5(file_md5(path))
                if f["path"] != os.path.abspath(path)]
        if not same:
            return None
        action = self._resolve_duplicate(path, same, "identical file")
        if action == "keep_both":
            return None
        if action == "replace":
            self._trash_others(same, path)
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
            self.source.name, candidate.source_id, keys, path)]
        if not others:
            return "none", []
        return self._resolve_duplicate(path, others, "album already linked"), others

    def _matches(self, path) -> list[Match]:
        return find_matches(self.source, path)

    def _select(self, path, matches: list[Match]) -> tuple[Optional[Candidate], Optional[float]]:
        strong, medium = self.settings.match.strong_threshold, self.settings.match.medium_threshold
        best = matches[0] if matches else None
        rec = recommend(best.score, strong, medium) if best else Recommendation.NONE
        never_ask = self.auto == "all" or self.pretend  # a preview must not stop at every file
        if rec is Recommendation.STRONG and (self.auto or never_ask or self.prompter is None):
            return best.candidate, best.score  # otherwise the user confirms it, pre-selected
        if never_ask:
            return (best.candidate, best.score) if rec is Recommendation.MEDIUM else (None, None)
        if self.prompter is None:
            return None, None
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
            self.progress("    album already complete in the library: nothing to fetch")
            return existing, False, "album already in library"
        if not fetch:
            if existing is not None:
                return existing, True, ""
            return self.library.add_album(self.source.partial_info(candidate)), True, ""
        self.progress(f"    fetching the album page ({self.source.name}; the first request waits a few seconds)...")
        started = time.monotonic()
        try:
            info = self.source.album_info(candidate.url, recache=self.recache)
            partial, note = False, ""
            self.progress(f"    page read in {time.monotonic() - started:.1f}s")
        except self.source.fetch_errors as err:
            self.progress(f"    page unavailable after {time.monotonic() - started:.1f}s: {err}")
            logger.warning(f"Page for {candidate.url} unavailable ({err}); using sitemap data only")
            if existing_full:
                return existing, False, "kept earlier metadata"
            info = self.source.partial_info(candidate)
            partial, note = True, f"partial: save the page {candidate.url} to import full metadata"
        album_id = self.library.add_album(info)
        self.library.prune_orphan_series()
        return album_id, partial, note
