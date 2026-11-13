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
    readable: bool = True  # False when the archive can not even be opened (nothing can be done with it)
    problem: Optional[str] = None  # why it is corrupted, in words
    embedded: Optional[dict] = None  # the simple fields of the file's own ComicInfo.xml


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
    """Yield absolute paths of comic files below ``root``, sorted.

    Hidden folders (``.bdnex-trash``...) are not searched: what is in them is not part of the collection.
    """
    root = Path(root)
    paths = [
        p.absolute().as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in COMIC_EXTENSIONS
        and not any(part.startswith(".") for part in p.relative_to(root).parts[:-1])
    ]
    yield from sorted(paths)


def _count_images(names) -> int:
    return sum(1 for n in names if n.lower().endswith(IMAGE_EXTENSIONS))


def _embedded(archive, names) -> Optional[dict]:
    """The simple fields of the ComicInfo.xml inside an archive (``None``: none, or unreadable)."""
    from bdnex.organizer.comicinfo import parse_xml

    name = next((n for n in names if os.path.basename(n).lower() == "comicinfo.xml"), None)
    if name is None:
        return None
    try:
        return parse_xml(archive.read(name)) or None
    except Exception as err:  # a broken ComicInfo.xml must not make the file unreadable
        logger.debug(f"{name} could not be parsed: {err}")
        return None


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
    embedded = None
    readable = True
    problem = None

    try:
        if fmt == "cbz":
            with zipfile.ZipFile(path) as zf:
                bad = zf.testzip()
                if bad is not None:
                    corrupted, problem = True, f"page {bad} failed its checksum"
                names = zf.namelist()
                page_count = _count_images(names)
                has_metadata = any(os.path.basename(n) == "ComicInfo.xml" for n in names)
                embedded = _embedded(zf, names)
        elif fmt == "cbr":
            with rarfile.RarFile(path) as rf:
                names = rf.namelist()
                page_count = _count_images(names)
                has_metadata = any(os.path.basename(n) == "ComicInfo.xml" for n in names)
                embedded = _embedded(rf, names)
        elif fmt == "pdf":
            import pymupdf

            with pymupdf.open(path) as doc:
                page_count = doc.page_count
        else:
            fmt = "unknown"
            corrupted, readable, problem = True, False, "not a CBZ, CBR or PDF file"
    except Exception as err:  # broken archive, missing unrar tool, ...
        logger.warning(f"{path} could not be read: {err}")
        corrupted, readable, problem = True, False, f"can not be opened: {err}"
        fmt = fmt or "unknown"

    return FileInfo(path=path, format=fmt, size=stat.st_size, mtime=stat.st_mtime,
                    page_count=page_count, corrupted=corrupted, has_metadata=has_metadata,
                    readable=readable, problem=problem, embedded=embedded)


def read_embedded(path) -> Optional[dict]:
    """The simple fields of a file's own ComicInfo.xml (``None``: no such file, no ComicInfo.xml, or unreadable)."""
    try:
        fmt = detect_format(path)
        if fmt == "cbz":
            with zipfile.ZipFile(path) as archive:
                return _embedded(archive, archive.namelist())
        if fmt == "cbr":
            with rarfile.RarFile(path) as archive:
                return _embedded(archive, archive.namelist())
    except Exception as err:
        logger.debug(f"{path}: no readable ComicInfo.xml ({err})")
    return None
