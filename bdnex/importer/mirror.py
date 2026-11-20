"""Keep the whole catalogue of the source in the library: every album page, its series page and its cover.

``bdnex mirror``. The album list (the sitemaps) says what exists; the library says what is already here. What is missing
(or only matched, its page never read) is fetched through the same page queue as an import (several endpoints at once,
the polite delay of each request), the album is built from the stored pages and its cover is saved as a file. Only the calling
thread touches SQLite. Nothing is lost when it stops: the library is the state, so the next run goes on with what is left.
"""

from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from bdnex.importer import covers

logger = logging.getLogger(__name__)

STOP_AFTER_FAILURES = 10  # failures in a row: the site is probably blocking, so do not hammer it


@dataclass
class Plan:
    in_list: int = 0  # albums of the album list (sitemaps)
    complete: int = 0  # in the library with their page read
    missing: list = field(default_factory=list)  # candidates not in the library at all
    unread: list = field(default_factory=list)  # in the library, only matched: their page was never read
    no_cover: list = field(default_factory=list)  # (source_id, cover address) of albums read but without a saved cover

    @property
    def to_fetch(self) -> list:
        """The pages to download, the albums not in the library first."""
        return [*self.missing, *self.unread]


@dataclass
class Result:
    albums: int = 0  # album records built (new or completed)
    covers: int = 0  # covers saved
    failed: list = field(default_factory=list)  # (address, why)
    stopped: str = ""  # why it stopped early, if it did
    left: int = 0  # pages not asked for because of the limit or the stop


def plan(source, library) -> Plan:
    """What the album list has that the library lacks (offline)."""
    states = library.album_states(source.name)
    result = Plan()
    for candidate in source.catalogue():
        result.in_list += 1
        state = states.get(candidate.source_id)
        if state is None:
            result.missing.append(candidate)
        elif state[0]:
            result.unread.append(candidate)
        else:
            result.complete += 1
    listed = {candidate.source_id for candidate in source.catalogue()}
    result.no_cover = [(sid, state[2]) for sid, state in states.items() if not state[0] and not state[1] and state[2] and sid in listed]
    return result


class Mirror:
    """Fetch what a :class:`Plan` lists. ``progress(done, total, detail)`` is told about each album and cover."""

    def __init__(self, source, library, settings, workers: Optional[int] = None, with_covers: bool = True,
                 progress: Callable[[int, int, str], None] = lambda done, total, detail: None):
        self.source, self.library, self.settings = source, library, settings
        self.workers = max(1, workers or getattr(source, "parallel", 1))
        self.with_covers = with_covers
        self.progress = progress
        self.result = Result()
        self._streak = 0

    # ---------------------------------------------------------------- covers
    def _cover_wanted(self, source_id: str, cover_url: Optional[str]) -> bool:
        return bool(self.with_covers and cover_url and covers.find_cover(self.settings.cover_path, self.source.name, source_id) is None)

    def _save_cover(self, source_id: str, data: bytes) -> None:
        covers.check_cover_image(data)  # never keep what is not a picture
        relative = covers.save_cover(self.settings.cover_path, self.source.name, source_id, data)
        album_id = self.library.find_album(self.source.name, source_id)
        if album_id is not None:
            self.library.set_cover_file(album_id, relative)
        self.result.covers += 1

    # ------------------------------------------------------------------- run
    def run(self, candidates: list, cover_only: list = (), limit: int = 0) -> Result:
        """Download the pages of ``candidates`` (at most ``limit`` when it is not 0) and the covers of ``cover_only``
        (``(source_id, address)``). Ctrl-C stops cleanly: what has arrived is kept."""
        todo = list(candidates[:limit] if limit else candidates)
        self.result.left = len(candidates) - len(todo)
        total = len(todo) + len(cover_only)
        done = 0
        queue = self.source.page_queue(self.workers)
        pool = ThreadPoolExecutor(max_workers=min(self.workers, 4), thread_name_prefix="bdnex-cover")
        covers_waiting: deque = deque()  # (source_id, future, whether it is one of `cover_only`: those are counted in the progress)
        by_series: dict[str, list] = {}  # series page address -> the album addresses waiting for it
        pages, loose = deque(todo), deque(cover_only)
        window = self.workers * 3  # how much is asked for ahead: bounds the pages held in memory
        try:
            while True:
                while pages and not self.result.stopped and queue.outstanding() + sum(map(len, by_series.values())) < window:
                    url = pages.popleft().url
                    if queue.submit(url) == "cached":
                        done += self._page_ready(queue, url, by_series, pool, covers_waiting)
                while loose and not self.result.stopped and len(covers_waiting) < window:
                    source_id, address = loose.popleft()
                    covers_waiting.append((source_id, pool.submit(self.source.fetch_cover, address), True))
                for kind, url, error in queue.wait(0.5):
                    if kind == "album":
                        done += (self._failed(url, error) if error is not None
                                 else self._page_ready(queue, url, by_series, pool, covers_waiting))
                    else:  # a series page: the albums that waited for it
                        for album_url in by_series.pop(url, []):
                            done += (self._failed(album_url, error) if error is not None
                                     else self._complete(album_url, pool, covers_waiting))
                done += self._harvest(covers_waiting)
                self.progress(done, total, "")
                in_flight = queue.outstanding() or by_series or covers_waiting
                if not in_flight and (self.result.stopped or not (pages or loose)):
                    break
        except KeyboardInterrupt:
            self.source.stop_requests()
            self.result.stopped = "stopped (Ctrl-C): what arrived is kept, run it again to go on"
        finally:
            self.result.left += len(pages)
            try:
                for kind, url, error in queue.store():
                    if kind == "album" and error is None:
                        self._page_ready(queue, url, by_series, pool, covers_waiting)
            except Exception as err:  # leaving must not fail
                logger.debug(f"could not store the last pages: {err}")
            queue.close()
            pool.shutdown(wait=False, cancel_futures=True)
            self._harvest(covers_waiting)
            self.library.prune_orphan_series()
        return self.result

    # --------------------------------------------------------------- helpers
    def _page_ready(self, queue, url: str, by_series: dict, pool, covers_waiting) -> int:
        """An album page is stored: ask for its series page if it is not here, else build the album."""
        try:
            href = self.source.series_href_of(url)
        except Exception as err:  # a page that does not parse
            return self._failed(url, err)
        state = "cached" if href is None else queue.submit(href, "series")
        if state == "cached":
            return self._complete(url, pool, covers_waiting)
        if state == "failed":
            return self._failed(url, RuntimeError("its series page did not come"))
        by_series.setdefault(href, []).append(url)  # "new" or "queued": the series event completes it
        return 0

    def _complete(self, url: str, pool, covers_waiting) -> int:
        """Both pages are in the library: build the album (no request) and ask for its cover."""
        try:
            info = self.source.album_info(url)
            self.library.add_album(info)
        except Exception as err:
            return self._failed(url, err)
        self._streak = 0
        self.result.albums += 1
        if pool is not None and self._cover_wanted(info.source_id, info.cover_url):
            covers_waiting.append((info.source_id, pool.submit(self.source.fetch_cover, info.cover_url), False))
        return 1

    def _failed(self, url: str, error) -> int:
        self._streak += 1
        self.result.failed.append((url, str(error) or error.__class__.__name__))
        if self._streak >= STOP_AFTER_FAILURES and not self.result.stopped:
            self.result.stopped = (f"stopped after {STOP_AFTER_FAILURES} failures in a row (is the site blocking? "
                                   f"last: {self.result.failed[-1][1]}): run it again later")
        return 1

    def _harvest(self, waiting: deque) -> int:
        """Save the covers that have arrived. Returns how many it dealt with."""
        dealt = 0
        for _ in range(len(waiting)):
            source_id, future, counted = waiting.popleft()
            if not future.done():
                waiting.append((source_id, future, counted))
                continue
            dealt += 1 if counted else 0
            try:
                self._save_cover(source_id, future.result())
            except Exception as err:  # a cover that did not come is not a reason to stop
                self.result.failed.append((f"cover of {source_id}", str(err) or err.__class__.__name__))
        return dealt

