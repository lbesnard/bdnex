"""Run a function over many items in several processes, results in the order of the items.

For work that is mostly waiting on a disk (reading and hashing files). The function must not touch a database: it runs in
a forked process. Anything that does is the caller's to do with the results, in the main process.
"""

from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Iterable, Iterator

MIN_ITEMS = 4  # fewer than this is not worth starting processes for


def can_fork() -> bool:
    return "fork" in multiprocessing.get_all_start_methods()


# The function is looked up in the child through this global (set before the fork): a lambda or a local function can be
# used, which pickling could not carry.
_FUNCTION: Callable[[Any], Any] | None = None


def _call(item):
    try:
        return _FUNCTION(item)
    except Exception as err:
        return err


def map_ordered(function: Callable[[Any], Any], items: Iterable[Any], workers: int = 1) -> Iterator[tuple[Any, Any]]:
    """``(item, result)`` for each item, in order; ``result`` is the exception when the function raised.

    With ``workers`` > 1 (and enough items, and a platform that can fork) the items are processed in several processes at
    once; otherwise one by one in this process, with the same results.
    """
    global _FUNCTION
    items = list(items)
    if workers > 1 and len(items) >= MIN_ITEMS and can_fork():
        _FUNCTION = function
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork")) as pool:
            yield from zip(items, pool.map(_call, items, chunksize=1))
    else:
        for item in items:
            try:
                yield item, function(item)
            except Exception as err:
                yield item, err
