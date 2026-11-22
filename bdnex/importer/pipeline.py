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
from typing import Callable, Iterable, Iterator, Optional, Union

from bdnex.importer.matching import Match, find_matches

MIN_PARALLEL = 8  # fewer files than this: a pool costs more than it saves
WORKER_TIMEOUT = 900  # seconds a worker may take for one file (reading and hashing a PDF of hundreds of MB, on a slow disk, is minutes)

_SOURCE = None  # set before the workers are forked; they read it from their copy of the process
_SIDE: Optional[Callable] = None  # idem: what else a worker does with each file (see ``match_ahead(side=)``)


def resolve_workers(configured: int) -> int:
    """``0`` means one process fewer than the CPU cores (the reviewing process needs one)."""
    return configured if configured > 0 else max(1, (os.cpu_count() or 2) - 1)


def _side_result(path: str):
    """The result of the side job for ``path``, or the exception it raised (the caller then does it itself)."""
    try:
        return _SIDE(path)
    except Exception as err:
        return err


def _match_in_worker(path: str):
    if not _SIDE:
        return find_matches(_SOURCE, path)
    try:
        matches = find_matches(_SOURCE, path)
    except Exception as err:  # a file that can not be matched is still read ahead: the caller reports the failure
        matches = err
    return matches, _side_result(path)


def can_fork() -> bool:
    return "fork" in multiprocessing.get_all_start_methods()


def match_ahead(source, paths: Iterable[str], workers: int = 1, lookahead: int = 100,
                min_parallel: int = MIN_PARALLEL, side: Optional[Callable] = None,
                side_results: Optional[dict] = None) -> Iterator[tuple[str, Union[list[Match], Exception]]]:
    """Yield ``(path, matches)`` in the order of ``paths``; a failure is yielded as the exception.

    ``side`` is a second job for each file, done by the same worker right after matching (reading and hashing the file,
    which is mostly waiting on the disk): its result (or its exception) is put in ``side_results[path]`` before the
    file is yielded. It must not touch a database.
    """
    global _SOURCE, _SIDE
    paths = list(paths)
    if side is not None and side_results is None:
        side_results = {}
    if workers <= 1 or len(paths) < min_parallel or not can_fork():
        return _one_by_one(source, paths, side, side_results)
    warm = getattr(source, "warm", None)
    if warm:
        warm()
    _SOURCE, _SIDE = source, side
    # a Pool forks all its workers right here, when this function is CALLED (a generator would wait for its first `next`, by which
    # time the caller may have started threads: forking from a multi-threaded process can deadlock)
    pool = multiprocessing.get_context("fork").Pool(processes=workers)
    return _served_by(pool, paths, lookahead, side, side_results)


def _one_by_one(source, paths, side, side_results) -> Iterator[tuple[str, Union[list[Match], Exception]]]:
    if True:
        for path in paths:
            try:
                found = find_matches(source, path)
            except Exception as err:
                found = err
            if side is not None:
                try:
                    side_results[path] = side(path)
                except Exception as err:
                    side_results[path] = err
            yield path, found


def _served_by(pool, paths, lookahead, side, side_results) -> Iterator[tuple[str, Union[list[Match], Exception]]]:
    global _SOURCE, _SIDE
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
                try:
                    found = pending.get(timeout=WORKER_TIMEOUT)
                except multiprocessing.TimeoutError:  # a worker killed (out of memory on a huge file) never answers: the file fails, the run goes on
                    raise TimeoutError(f"the matching worker did not answer for {WORKER_TIMEOUT // 60} minutes (killed? out of memory?)") from None
                if side is not None:
                    found, side_results[path] = found
                yield path, found
            except Exception as err:
                yield path, err
    finally:
        pool.terminate()
        _SOURCE = _SIDE = None
