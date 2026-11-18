"""Which program reads a RAR (CBR) file.

``rarfile`` has none of its own and takes the first that answers: ``unrar``, ``unar``, ``7z``, ``bsdtar``. A ``7z`` without the
RAR codec (the free p7zip, the newer ``7zip`` package) answers the "are you there?" test and then fails on every page
(``Unsupported Method``, which ``rarfile`` reports as a 36 to 62 byte read), so ``7z`` is the last choice here.
"""

from __future__ import annotations

import logging
import threading

import rarfile

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_chosen = False


def ensure() -> None:
    """Pick the tool once: any of ``unrar`` / ``unar`` / ``bsdtar`` first, ``7z`` only when there is nothing else."""
    global _chosen
    with _lock:
        if _chosen:
            return
        _chosen = True
        for kwargs in ({"sevenzip": False, "sevenzip2": False}, {}):
            try:
                rarfile.tool_setup(force=True, **kwargs)
                return
            except rarfile.RarCannotExec:
                continue
        logger.debug("no program to read RAR files (install unrar)")


def RarFile(path: str):  # noqa: N802  (stands where ``rarfile.RarFile`` stood)
    ensure()
    return rarfile.RarFile(path)
