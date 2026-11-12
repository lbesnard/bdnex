"""Match file names in the background while the user reviews earlier files.

Matching is pure CPU and needs no network or database, so it can run in worker processes. The
caller walks the files in order and takes each result as it is needed; the pool keeps matching up
to ``lookahead`` files ahead, so reviewing a file never makes the matching wait, and a slow
decision never delays it.

Workers are forked (all at once, up front), so they inherit the already loaded index of the source for free. Where
``fork`` does not exist (Windows), or for a few files, matching runs in the calling process.
"""

from __future__ import annotations

import collections
import multiprocessing
import os
from typing import Iterable, Iterator, Union

from bdnex.importer.matching import Match, find_matches

MIN_PARALLEL = 8  # fewer files than this: a pool costs more than it saves

_SOURCE = None  # set before the workers are forked; they read it from their copy of the process


def resolve_workers(configured: int) -> int:
    """``0`` means one process fewer than the CPU cores (the reviewing process needs one)."""
    return configured if configured > 0 else max(1, (os.cpu_count() or 2) - 1)


def _match_in_worker(path: str) -> list[Match]:
    return find_matches(_SOURCE, path)


def can_fork() -> bool:
    return "fork" in multiprocessing.get_all_start_methods()


def match_ahead(source, paths: Iterable[str], workers: int = 1, lookahead: int = 100,
                min_parallel: int = MIN_PARALLEL) -> Iterator[tuple[str, Union[list[Match], Exception]]]:
    """Yield ``(path, matches)`` in the order of ``paths``; a failure is yielded as the exception."""
    global _SOURCE
    paths = list(paths)
    if workers <= 1 or len(paths) < min_parallel or not can_fork():
        for path in paths:
            try:
                yield path, find_matches(source, path)
            except Exception as err:
                yield path, err
        return

    warm = getattr(source, "warm", None)
    if warm:
        warm()
    _SOURCE = source
    # a Pool forks all its workers right here, before any helper thread exists (forking later,
    # from a multi-threaded process, can deadlock)
    pool = multiprocessing.get_context("fork").Pool(processes=workers)
    try:
        remaining = iter(paths)
        window: collections.deque = collections.deque()

        def top_up():
            while len(window) < lookahead:
                path = next(remaining, None)
                if path is None:
                    return
                window.append((path, pool.apply_async(_match_in_worker, (path,))))

        top_up()
        while window:
            path, pending = window.popleft()
            top_up()  # refill before handing over: the workers go on while the caller reviews
            try:
                yield path, pending.get()
            except Exception as err:
                yield path, err
    finally:
        pool.terminate()
        _SOURCE = None
