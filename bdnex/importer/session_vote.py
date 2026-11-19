"""A second chance for the files nobody could decide: their cover votes.

With ``import --auto=all`` (never ask) and ``match.auto_all: strong``, a match in the middle (a score between the two
thresholds, or a strong one with another album close behind) is not applied: the file stays skipped. Its best
candidates are then compared by cover, at the end of the run and at low priority: the cover is evidence the title
score does not use. A candidate wins when its cover looks like the file's (``match.cover_vote_min``) and clearly more
than the others'; otherwise the file stays skipped (``bdnex import --skipped`` or ``bdnex candidates`` come back to it).
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from bdnex.importer import covers
from bdnex.importer.matching import Match
from bdnex.importer.session_types import ImportReport, Journal

logger = logging.getLogger(__name__)

CANDIDATES = 3  # how many of the best candidates are compared
LEAD = 15.0  # points the winner's cover must be ahead of the next one


class CoverVote:
    """Methods of :class:`~bdnex.importer.session.ImportSession` (a mixin: its state is ``_undecided``)."""

    def _wants_vote(self) -> bool:
        return bool(self.settings.match.cover_vote and self.cover_check and not self._cover_off and not self.pretend)

    def _queue_vote(self, path: str, matches: list[Match]) -> None:
        if self._wants_vote():
            self._undecided.append((path, matches))

    def _candidate_cover(self, candidate, fetched: dict) -> Optional[bytes]:
        """The cover of a candidate: the saved file when its album is known, else what ``fetched`` got for it."""
        known = self.library.find_album(self.source.name, candidate.source_id)
        album = self.library.get_album(known) if known else None
        kept = self._kept_cover(album) if album else None
        if kept is not None:
            try:
                return kept.read_bytes()
            except OSError:
                return None
        return fetched.get(candidate.source_id)

    def _cover_urls(self, pool: list[Match]) -> dict[str, str]:
        """The cover addresses still to be requested: ``{source id: address}`` (none for a candidate whose cover is saved)."""
        urls = {}
        for match in pool:
            known = self.library.find_album(self.source.name, match.candidate.source_id)
            album = self.library.get_album(known) if known else None
            if album and self._kept_cover(album) is not None:
                continue
            guess = getattr(self.source, "guess_cover_url", None)
            url = (album or {}).get("cover_url") or (guess(match.candidate) if guess else None)
            if url:
                urls[match.candidate.source_id] = url
        return urls

    def _vote(self, path: str, matches: list[Match]):
        """``(candidate, score, similarity)`` of the candidate whose cover wins, or ``None``."""
        settings = self.settings.match
        pool = [m for m in matches if m.score >= settings.medium_threshold][:CANDIDATES]
        if not pool:
            return None
        row = self.library.get_file(path)
        local = covers.front_cover(path, (row or {}).get("format") or os.path.splitext(path)[1].lstrip(".").lower())
        if not local:
            return None
        fetch = getattr(self.source, "fetch_cover", None) or getattr(self.source, "cover_image", None)
        fetched: dict[str, bytes] = {}
        urls = self._cover_urls(pool)
        if urls and fetch is not None:
            with ThreadPoolExecutor(max_workers=len(urls), thread_name_prefix="bdnex-vote") as executor:
                futures = {source_id: executor.submit(fetch, url) for source_id, url in urls.items()}
            for source_id, future in futures.items():
                try:
                    data = future.result()
                    covers.check_cover_image(data)
                    fetched[source_id] = data
                except (covers.CoverError, *self.source.fetch_errors) as err:
                    logger.debug(f"{path}: cover of {urls[source_id]} not compared ({err})")
        scored = []
        for match in pool:
            online = self._candidate_cover(match.candidate, fetched)
            if online is None:
                continue
            try:
                scored.append((covers.sift_similarity(local, online), match))
            except covers.CoverUnavailable:
                self._cover_off = True
                return None
            except covers.CoverError as err:
                logger.debug(f"{path}: cover not compared ({err})")
        if not scored:
            return None
        scored.sort(key=lambda pair: -pair[0])
        best, winner = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        if best < settings.cover_vote_min or best - second < LEAD:
            self.detail(f"    cover vote: no clear winner for {os.path.basename(path)} (best cover {best:.0f}%, next {second:.0f}%)")
            return None
        return winner.candidate, winner.score, best

    def _cover_votes(self, report: ImportReport) -> None:
        """The low-priority pass: decide, by cover, the files the run left undecided."""
        waiting, self._undecided = self._undecided, []
        if not waiting or not self._wants_vote():
            return
        self.progress(f"Comparing covers to decide {len(waiting)} undecided file(s) (low priority; Ctrl-C stops, "
                      "`bdnex import --skipped` tries again)...")
        decided = 0
        try:
            for number, (path, matches) in enumerate(waiting, start=1):
                if number % 25 == 0:
                    self.progress(f"  cover vote: {number}/{len(waiting)}, {decided} decided")
                winner = self._vote(path, matches)
                if winner is None:
                    continue
                candidate, score, similarity = winner
                self._journal = Journal()
                try:
                    result = self._import_matched(path, matches, False, (candidate, score, f"cover vote {similarity:.0f}%"))
                finally:
                    journal, self._journal = self._journal, None
                result.journal = journal
                for index, old in enumerate(report.results):
                    if old.path == path:
                        report.results[index] = result
                        break
                else:
                    report.results.append(result)
                decided += 1
        except KeyboardInterrupt:
            self.progress("Interrupted: the other undecided files stay skipped (`bdnex import --skipped` tries again).")
        if decided:
            self.progress(f"The covers decided {decided} of {len(waiting)} undecided file(s).")
