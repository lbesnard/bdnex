"""Inspect a comic file on disk: format, page count, integrity."""

from __future__ import annotations

import logging
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import rarfile

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
COMIC_EXTENSIONS = (".cbz", ".cbr", ".pdf")


@dataclass
class FileInfo:
    path: str
    format: str  # cbz | cbr | pdf
    size: int
    mtime: float
    page_count: Optional[int]
    corrupted: bool
    has_metadata: bool


def detect_format(path) -> Optional[str]:
    """Detect cbz/cbr/pdf from the content (falling back on nothing)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        return "cbz"
    if head.startswith(b"Rar!"):
        return "cbr"
    return None


def iter_comic_files(root) -> Iterator[str]:
    """Yield absolute paths of comic files below ``root``, sorted."""
    paths = [
        p.absolute().as_posix()
        for p in Path(root).rglob("*")
        if p.is_file() and p.suffix.lower() in COMIC_EXTENSIONS
    ]
    yield from sorted(paths)


def _count_images(names) -> int:
    return sum(1 for n in names if n.lower().endswith(IMAGE_EXTENSIONS))


def inspect_file(path) -> FileInfo:
    """Return format, size, page count and integrity of a comic file.

    Never raises on a broken archive: it is reported as ``corrupted``.
    """
    path = os.path.abspath(path)
    stat = os.stat(path)
    fmt = detect_format(path)
    page_count = None
    corrupted = False
    has_metadata = False

    try:
        if fmt == "cbz":
            with zipfile.ZipFile(path) as zf:
                corrupted = zf.testzip() is not None
                names = zf.namelist()
                page_count = _count_images(names)
                has_metadata = any(os.path.basename(n) == "ComicInfo.xml" for n in names)
        elif fmt == "cbr":
            with rarfile.RarFile(path) as rf:
                names = rf.namelist()
                page_count = _count_images(names)
                has_metadata = any(os.path.basename(n) == "ComicInfo.xml" for n in names)
        elif fmt == "pdf":
            import pymupdf

            with pymupdf.open(path) as doc:
                page_count = doc.page_count
        else:
            fmt = "unknown"
            corrupted = True
    except Exception as err:  # broken archive, missing unrar tool, ...
        logger.warning(f"{path} could not be read: {err}")
        corrupted = True
        fmt = fmt or "unknown"

    return FileInfo(path=path, format=fmt, size=stat.st_size, mtime=stat.st_mtime,
                    page_count=page_count, corrupted=corrupted, has_metadata=has_metadata)
