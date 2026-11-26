"""Measure the sharpness (dpi) of the files already in the library: ``bdnex check --quality``.

A file registered by an import or a scan is measured as it is read; the ones that were already there (a library from before schema v18) or
whose ``quality.page_height_in`` changed are measured here, by several processes at once (read-only work: a worker never touches the database;
the numbers are written by the caller's process).
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Callable, Optional

from bdnex import quality
from bdnex.parallel import map_ordered


def pending(library, query: list[str], again: bool = False) -> list[dict]:
    """The rows of the files to measure: readable, on disk, and (unless ``again``) with no dpi yet. ``query``: the terms of ``ls --files``."""
    rows = library.query(query, files=True)
    return [row for row in rows if not row.get("corrupted") and os.path.exists(row["path"]) and (again or row.get("dpi") is None)]


def measure_rows(library, rows: list[dict], workers: int = 1, height_in: Optional[float] = None,
                 progress: Optional[Callable[[int, int], None]] = None) -> Counter:
    """Measure ``rows`` and store each dpi. Returns how many files fall in each tier (``not measured`` for a file with no pictures to measure)."""
    height = height_in or quality.page_height_in

    def one(row):
        return quality.measure(row["path"], row.get("format"), height)

    counts: Counter = Counter()
    for number, (row, result) in enumerate(map_ordered(one, rows, workers), start=1):
        dpi = None if isinstance(result, Exception) else result
        library.set_dpi(row["path"], dpi)
        counts[quality.tier_of(dpi)] += 1
        if progress:
            progress(number, len(rows))
    return counts
