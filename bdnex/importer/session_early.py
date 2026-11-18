"""The part of an import that fetches album and series pages while files are still being matched.

``Bedetheque.page_queue`` downloads in worker threads; only the session's own thread touches the library. The methods
here start the queue, hand it the pages of each linked file, and build an album's record from its stored pages as soon
as both its pages are here. See ``ImportSession`` for the rest of the run.
"""

from __future__ import annotations

import logging

from bdnex.importer.session_types import PARTIAL, TAGGED

logger = logging.getLogger(__name__)


class EarlyFetch:
    """Methods of :class:`~bdnex.importer.session.ImportSession` (a mixin: it keeps no state of its own)."""

    def _open_early(self) -> None:
        """Start downloading each album page as soon as its file is matched, instead of after the last file.

        The pages go through the same polite delay and ``parallel`` limit as before; only the waiting changes."""
        self._early = None
        getattr(self.source, "allow_requests", lambda: None)()  # a stop of an earlier run must not stop this one
        self._by_page, self._by_series, self._done, self._completed_early = {}, {}, set(), 0
        if "fetch" in self.steps and not self.pretend and not self.recache and hasattr(self.source, "page_queue"):
            self._early = self.source.page_queue(getattr(self.source, "parallel", 1))

    def _finish_stored(self) -> None:
        """Leaving early: complete the albums whose pages are already here (no request), drop the rest."""
        try:
            if self._early is not None:
                self._handle_arrivals(self._early.store())
        except Exception as err:  # leaving must not fail
            logger.debug(f"could not finish the stored pages: {err}")
        self._close_early()

    def _close_early(self) -> None:
        if self._early is not None:
            self._early.close()
            self._early = None

    def _queue_page(self, item) -> None:
        """Ask for the album page of a pending item now; the album is completed when the page (and its series page) is here."""
        if self._early is None:
            return
        url = item[1].url
        self._by_page.setdefault(url, []).append(item)
        self.detail(f"    page queued: {url.rsplit('/', 1)[-1]}")
        state = self._early.submit(url)
        if state == "cached":
            self._page_ready(url)
        elif state == "failed":
            self._by_page.pop(url, None)  # asked for before and it did not come: the fetch phase tries again

    def _handle_arrivals(self, events) -> None:
        """What to do with the pages that arrived: a page of an album needs its series page, which is asked for in
        turn; an album is completed (from the library's cache: no request) once both are here."""
        for kind, url, error in events:
            if kind == "album":
                if error is None:
                    self._page_ready(url)
                else:
                    self._by_page.pop(url, None)  # the fetch phase reports it
            else:
                items = self._by_series.pop(url, [])
                if error is None:
                    self._complete_early(items)

    def _page_ready(self, url: str) -> None:
        items = self._by_page.pop(url, [])
        if not items:
            return
        try:
            href = self.source.series_href_of(url)
        except Exception as err:  # a page that does not parse: the fetch phase says so for its file
            logger.debug(f"{url}: {err}")
            return
        state = "cached" if href is None else self._early.submit(href, "series")
        if state == "cached":
            self._complete_early(items)
        elif state != "failed":
            self._by_series.setdefault(href, []).extend(items)

    def _complete_early(self, items) -> None:
        journal, self._journal = self._journal, None  # what is recorded here belongs to no file being matched
        try:
            for item in items:
                if id(item[0]) in self._done:
                    continue
                try:
                    self._complete(item)
                except Exception as err:  # the fetch phase reports it, with the file
                    logger.debug(f"{item[1].url}: {err}")
                    continue
                self._done.add(id(item[0]))
                self._completed_early += 1
                self.detail(f"    album completed: {item[1].url.rsplit('/', 1)[-1]}")
        finally:
            self._journal = journal
        self._take_covers()  # the covers that arrived meanwhile are weighed now

    def _complete(self, item) -> None:
        """Build the album's record from its stored pages and say so in the file's result."""
        result, candidate, damaged = item
        album_id, partial, note = self._album_for(candidate)
        result.album_id = album_id
        result.outcome = PARTIAL if partial else TAGGED
        result.note = "; ".join(n for n in (note, damaged) if n)
        self._log(result.path, result.outcome, result.score, album_id)
        if not partial:
            self._confirm(result)
        if not partial and (self.cover_check or self.settings.cover.download):
            self._queue_cover(result)  # downloaded in the background: nothing waits for it here

    def _drain_early(self) -> None:
        """The matching is over: wait for the pages still on their way, completing each album as its pages arrive."""
        waiting = sum(1 for items in (*self._by_page.values(), *self._by_series.values()) for i in items
                      if id(i[0]) not in self._done)
        if self._early is None or not waiting:
            return
        self.phase("fetch", f"Waiting for {waiting} album page(s) still on their way "
                            "(Ctrl-C stops and keeps what is complete; `bdnex import --fetch` resumes)")
        bar = self.bar(waiting, "Album pages") if self.bar else None
        shown = self._completed_early
        try:
            while self._early.outstanding():
                self._handle_arrivals(self._early.wait(0.5))
                names = getattr(self.source, "fetching_now", lambda: [])()
                if bar and self._completed_early > shown:
                    bar.update(" · ".join(names), step=self._completed_early - shown)
                elif bar:
                    bar.refresh(" · ".join(names))
                shown = self._completed_early
        finally:
            if bar:
                bar.finish()
