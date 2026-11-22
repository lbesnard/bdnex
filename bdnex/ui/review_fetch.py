"""The background fetching of the review screen: pages and covers downloaded several at a time, the library written one file at a time.

A file linked in the screen has its album page, its series page and its cover to fetch. With several endpoints (``bedetheque.parallel``,
or one per ``fetcher_endpoint``, as ``bdnex import`` uses them) the downloads of several files run at once, each waiting its own polite
delay. Only the downloading is parallel: ONE thread, this queue's, stores the pages and then runs each file's import (``rematch``), which
finds everything stored and makes no request. So two files of one series never write the same series or album row at the same time.

With one worker (one endpoint, or saved pages) nothing is prefetched: each file's import fetches for itself, one file at a time.
This module knows nothing of the screen (no Textual): the screen hands it :class:`Job` objects with callbacks.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Optional

from bdnex.importer.bedetheque import page_workers

logger = logging.getLogger("bdnex.review")


@dataclass
class Job:
    """One linked file whose page and cover are to be fetched."""

    url: str  # its album's page
    path: str
    wanted: Callable[[], bool]  # False once the person decided the file again: nothing is fetched or imported for it
    started: Callable[[], None]  # its downloads begin (the row says "fetching")
    finished: Callable[[str], None]  # it is over: the error in words, or "" (called from this queue's thread)
    run_import: Callable[[], bool]  # its import: reads the stored pages, writes the album and the cover file; False: it failed
    error: str = ""
    cover: Optional[object] = field(default=None, repr=False)  # the Future of its cover download, when there is one
    pages_ready: bool = False


class DetailsQueue:
    """Jobs in, one thread that owns the library, up to ``workers`` downloads at once. ``submit`` never blocks."""

    def __init__(self, settings, make_source: Callable, workers: Optional[int] = None):
        self.settings, self.make_source = settings, make_source
        self.workers = workers if workers is not None else page_workers(settings)
        self._jobs: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.source = None  # built in the thread (its library connection stays there)

    def submit(self, job: Job) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="bdnex-details", daemon=True)
                self._thread.start()
        self._jobs.put(job)

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        """Stop: what is not started is dropped, and the requests waiting their polite delay give up (those in flight end by themselves)."""
        if cancel_futures:
            self._cancel.set()
            stop = getattr(self.source, "stop_requests", None)
            if stop:
                stop()
        self._jobs.put(None)
        if wait and self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------ the thread
    def _run(self) -> None:
        library = pages = covers = None
        if self.workers > 1:
            try:
                from bdnex.library import Library

                library = Library.for_settings(self.settings)
                self.source = self.make_source(library)
                if hasattr(self.source, "page_queue"):
                    pages = self.source.page_queue(self.workers)
                    covers = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="bdnex-details-cover")
            except Exception as err:  # no parallel fetching: each import fetches for itself, one at a time
                logger.warning("the pages cannot be fetched in parallel (%s): one file at a time", err)
                pages = None
        try:
            self._loop(pages, covers)
        finally:
            if pages is not None:
                pages.close()
            if covers is not None:
                covers.shutdown(wait=False, cancel_futures=True)
            if library is not None:
                library.close()

    def _loop(self, pages, covers) -> None:
        self._pages, self._covers = pages, covers
        self._by_page: dict[str, list[Job]] = {}  # album page address -> the jobs waiting for it
        self._by_series: dict[str, list[Job]] = {}  # series page address -> the jobs waiting for it
        self._saved: dict[str, object] = {}  # album id -> the Future of its cover (files of one album share it)
        pending: collections.deque = collections.deque()
        active: list[Job] = []
        stop = False
        capacity = self.workers if pages is not None else 1
        while not (stop and not pending and not active):
            try:
                item = self._jobs.get(timeout=None if not (pending or active) else 0.05)
                while True:
                    if item is None:
                        stop = True
                    else:
                        pending.append(item)
                    item = self._jobs.get_nowait()
            except queue.Empty:
                pass
            if self._cancel.is_set():
                return
            while pending and len(active) < capacity:  # start the downloads of as many files as there are endpoints
                job = pending.popleft()
                if not job.wanted():
                    self._finish(job, "")
                    continue
                job.started()
                if pages is None:
                    self._import(job)
                    continue
                active.append(job)
                self._ask_album(job)
            if pages is not None and active:
                for kind, address, err in pages.wait(0.3):
                    self._arrived(kind, address, err)
                for job in list(active):
                    if job.pages_ready and (job.cover is None or job.cover.done()):
                        active.remove(job)
                        self._import(job)

    def _ask_album(self, job: Job) -> None:
        state = self._pages.submit(job.url)
        if state == "cached":
            self._album_here(job)  # stored already: on to its series page
        elif state == "failed":
            job.error = "its page could not be fetched"
            job.pages_ready = True  # the import tries again, and says why in the log
        else:
            self._by_page.setdefault(job.url, []).append(job)

    def _arrived(self, kind: str, address: str, err) -> None:
        jobs = (self._by_page if kind == "album" else self._by_series).pop(address, [])
        for job in jobs:
            if err is not None:
                job.error = f"page not fetched: {err}"
                job.pages_ready = True  # its import asks once more by itself, and reports the failure in the log
            elif kind == "album":
                self._album_here(job)
            else:
                self._series_here(job)

    def _album_here(self, job: Job) -> None:
        """The album page is stored: its series page is next (a one-shot has none), then its cover."""
        try:
            href = self.source.series_href_of(job.url)
        except Exception as err:  # a page that does not parse: the import says so for its file
            logger.debug("%s: %s", job.url, err)
            job.pages_ready = True
            return
        state = "cached" if href is None else self._pages.submit(href, "series")
        if state in ("cached", "failed"):
            self._series_here(job)
        else:
            self._by_series.setdefault(href, []).append(job)

    def _series_here(self, job: Job) -> None:
        job.pages_ready = True
        if self._covers is None or not getattr(self.settings.cover, "download", False):
            return
        try:
            from bdnex.importer import covers as cover_files
            from bdnex.importer.bedetheque import sitemap

            album_id = sitemap.source_id_from_url(job.url)
            cover_url = self.source.cover_url_of(SimpleNamespace(url=job.url))  # from the stored page: no request
            if not (album_id and cover_url) or cover_files.find_cover(self.settings.cover_path, self.source.name, album_id):
                return
            if album_id not in self._saved:
                self._saved[album_id] = self._covers.submit(self._save_cover, cover_url, album_id)
            job.cover = self._saved[album_id]
        except Exception as err:  # a cover is a help: the import gets it (or not) by itself
            logger.debug("%s: no cover prefetch (%s)", job.url, err)

    def _save_cover(self, url: str, album_id: str) -> None:
        """Worker thread: request the cover and keep it as a file (touches no database; the import then finds it saved)."""
        from bdnex.importer import covers as cover_files

        data = self.source.fetch_cover(url)
        cover_files.check_cover_image(data)
        cover_files.save_cover(self.settings.cover_path, self.source.name, album_id, data)

    def _import(self, job: Job) -> None:
        error = ""
        if job.wanted():  # not decided again while it downloaded
            try:
                if job.run_import() is False:
                    error = job.error or "the import failed, see the log"
            except BaseException as err:  # one file's failure never stops the others
                error = str(err) or err.__class__.__name__
        self._finish(job, error)

    @staticmethod
    def _finish(job: Job, error: str) -> None:
        try:
            job.finished(error)
        except Exception:  # the screen is gone
            logger.debug("%s: nobody to tell that it is over", job.path)
