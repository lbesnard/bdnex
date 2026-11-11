"""The import session: match files to albums and record the result in the library.

Pipeline per file: skip if already imported -> corruption/duplicate-file checks ->
local candidates -> score -> (ask) -> fetch metadata, falling back to what the
sitemap URL alone tells -> store album + link file -> log.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional, Protocol

from bdnex.config import Settings
from bdnex.importer.bedetheque import Bedetheque
from bdnex.importer.bedetheque.bedetheque import BlockedError
from bdnex.importer.bedetheque.sitemap import Candidate
from bdnex.importer.matching import (Match, Recommendation, query_from_filename, recommend, rerank,
                                     slug_album_info)
from bdnex.library import Library
from bdnex.library.hashing import file_md5
from bdnex.library.scan import inspect_file, iter_comic_files

logger = logging.getLogger(__name__)

TAGGED, PARTIAL, SKIPPED, DUPLICATE, FAILED, PRETEND = (
    "tagged", "partial", "skipped", "duplicate", "failed", "pretend")

# errors that mean "this page cannot be fetched now": fall back to the sitemap data
FETCH_ERRORS = (BlockedError, FileNotFoundError, OSError)


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

    def count(self, outcome: str) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)


class ImportSession:
    def __init__(self, library: Library, source: Bedetheque, settings: Settings,
                 prompter: Optional[Prompter] = None, pretend: bool = False,
                 force: bool = False, recache: bool = False):
        self.library = library
        self.source = source
        self.settings = settings
        self.prompter = prompter
        self.pretend = pretend
        self.force = force
        self.recache = recache

    # ------------------------------------------------------------------ run
    def run(self, paths: list[str]) -> ImportReport:
        report = ImportReport()
        files = []
        for path in paths:
            if not os.path.exists(path):  # say so now, before any network access
                report.results.append(ImportResult(os.path.abspath(path), FAILED, note="no such file or folder"))
            else:
                files.extend(self._collect([path]))
        if not files:
            return report
        if self.source.sitemaps_cached() == 0 or self.recache:
            logger.info("Updating Bédéthèque sitemaps")
            self.source.update_sitemaps(recache=self.recache)
        for path in files:
            try:
                result = self.import_file(path)
            except Exception as err:  # one bad file must not stop the batch
                logger.exception(f"{path} failed")
                result = ImportResult(path, FAILED, note=str(err))
                if not self.pretend:
                    self.library.log_import(path, FAILED)
            report.results.append(result)
        return report

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
    def import_file(self, path: str) -> ImportResult:
        known = self.library.get_file(path)
        if known and known["album_id"] is not None and self.settings.import_.resume and not self.force:
            return ImportResult(path, SKIPPED, album_id=known["album_id"], note="already imported")

        info = inspect_file(path)
        if info.corrupted:
            self._log(path, FAILED)
            return ImportResult(path, FAILED, note="corrupted or unreadable archive")

        dup = self._duplicate_file(path)
        if dup is not None:
            return dup

        matches = self._matches(path)
        candidate, score = self._select(path, matches)
        if candidate is None:
            self._log(path, SKIPPED)
            return ImportResult(path, SKIPPED, note="no match")

        if self.pretend:
            return ImportResult(path, PRETEND, score=score,
                                note=f"would link to {candidate.url}")

        album_action = self._duplicate_album(path, candidate)
        if album_action == "skip":
            self._log(path, DUPLICATE, score)
            return ImportResult(path, DUPLICATE, score=score, note="album already in library")

        album_id, partial, note = self._album_for(candidate)
        if album_action == "replace":
            for other in self.library.files_for_album(album_id):
                if other["path"] != os.path.abspath(path):
                    self.library.set_file_album(other["path"], None)
        self.library.add_file(path, album_id)
        outcome = PARTIAL if partial else TAGGED
        self._log(path, outcome, score, album_id)
        return ImportResult(path, outcome, score=score, album_id=album_id, note=note)

    # -------------------------------------------------------------- helpers
    def _log(self, path, outcome, score=None, album_id=None):
        if not self.pretend:
            self.library.log_import(path, outcome, score, album_id)

    def _resolve_duplicate(self, path, others, what) -> str:
        action = self.settings.duplicates.action
        if action != "ask":
            return action
        if self.force or self.prompter is None:
            return "skip"  # never silently create/replace duplicates
        return self.prompter.duplicate_action(path, others, what)

    def _duplicate_file(self, path) -> Optional[ImportResult]:
        """Same bytes already registered under another path."""
        same = [f["path"] for f in self.library.files_with_md5(file_md5(path))
                if f["path"] != os.path.abspath(path)]
        if not same:
            return None
        action = self._resolve_duplicate(path, same, "identical file")
        if action == "keep_both":
            return None
        self._log(path, DUPLICATE)
        return ImportResult(path, DUPLICATE, note=f"identical to {same[0]}")

    def _duplicate_album(self, path, candidate: Candidate) -> str:
        """Another file for the same album, or for an album with the same `duplicates.keys`
        (e.g. two editions of the same tome). Returns skip | keep_both | replace."""
        info = slug_album_info(candidate)
        available = {"series": info.series.name if info.series else None, "volume": info.volume,
                     "title": info.title}
        keys = {k: available[k] for k in self.settings.duplicates.keys if available.get(k) is not None}
        # a one-shot has no series/volume: only an identical album counts, never "all one-shots"
        if not info.series:
            keys = {k: v for k, v in keys.items() if k == "title"}
        others = [f["path"] for f in self.library.files_matching_album(
            "bedetheque", candidate.source_id, keys, path)]
        if not others:
            return "keep_both"
        return self._resolve_duplicate(path, others, "album already linked")

    def _matches(self, path) -> list[Match]:
        query = query_from_filename(path)
        return rerank(query, self.source.candidates(query, limit=5))

    def _select(self, path, matches: list[Match]) -> tuple[Optional[Candidate], Optional[float]]:
        strong, medium = self.settings.match.strong_threshold, self.settings.match.medium_threshold
        best = matches[0] if matches else None
        rec = recommend(best.score, strong, medium) if best else Recommendation.NONE
        if rec is Recommendation.STRONG:
            return best.candidate, best.score
        if self.force:
            return (best.candidate, best.score) if rec is Recommendation.MEDIUM else (None, None)
        if self.prompter is None:
            return None, None
        chosen = self.prompter.choose_match(path, matches)
        if chosen is None:
            return None, None
        score = next((m.score for m in matches if m.candidate == chosen), None)
        return chosen, score

    def _album_for(self, candidate: Candidate) -> tuple[int, bool, str]:
        """Return (album_id, partial, note), storing the album when it is new or refreshed."""
        existing = self.library.find_album("bedetheque", candidate.source_id)
        existing_full = (existing is not None
                         and not self.library.get_album(existing)["extra"].get("partial"))
        if existing_full and not self.recache:
            return existing, False, "album already in library"
        try:
            info = self.source.album_info(candidate.url, recache=self.recache)
            partial, note = False, ""
        except FETCH_ERRORS as err:
            logger.warning(f"Page for {candidate.url} unavailable ({err}); using sitemap data only")
            if existing_full:
                return existing, False, "kept earlier metadata"
            info = slug_album_info(candidate)
            partial, note = True, f"partial: save the page {candidate.url} to import full metadata"
        album_id = self.library.add_album(info)
        self.library.prune_orphan_series()
        return album_id, partial, note
