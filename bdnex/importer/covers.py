"""Front covers: take one out of a comic file, and tell whether two files have the same one.

:func:`cover_hash` / :func:`hash_distance` answer "are these two files the same book?". A 64-bit
perceptual hash of the cover is tiny, needs no extra dependency and is stored in the library, so
thousands of files can be compared at once. The same pictures in another container (CBZ, CBR, PDF)
give almost the same hash.
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
import zipfile
from typing import Optional

import rarfile

from bdnex.organizer.archive import IMAGE_EXTS, PDF_EXTRACTABLE

logger = logging.getLogger(__name__)

HASH_BITS = 64
SAME_COVER_DISTANCE = 10  # bits of 64 that may differ between two copies of the same cover
PDF_COVER_DPI = 100


# ------------------------------------------------------------------ taking a cover out
def _natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _is_page(name: str) -> bool:
    base = posixpath.basename(name)
    return (name.lower().endswith(IMAGE_EXTS) and not base.startswith(".")
            and "__macosx" not in name.lower().split("/"))


def front_cover(path: str, fmt: str) -> Optional[bytes]:
    """The bytes of the first page image of a CBZ, CBR or PDF; ``None`` if there is none or the file is unreadable."""
    try:
        if fmt == "cbz":
            with zipfile.ZipFile(path) as archive:
                names = sorted((i.filename for i in archive.infolist() if not i.is_dir() and _is_page(i.filename)),
                               key=_natural_key)
                return (archive.read(names[0]) or None) if names else None  # an empty page is no cover
        if fmt == "cbr":
            with rarfile.RarFile(path) as archive:
                names = sorted((i.filename for i in archive.infolist() if not i.is_dir()
                                and _is_page(i.filename.replace("\\", "/"))), key=_natural_key)
                return (archive.read(names[0]) or None) if names else None
        if fmt == "pdf":
            return _pdf_first_page(path)
    except Exception as err:  # a damaged archive simply has no usable cover
        logger.debug(f"no cover from {path}: {err}")
    return None


def _pdf_first_page(path: str) -> Optional[bytes]:
    import pymupdf

    with pymupdf.open(path) as doc:
        if doc.page_count == 0:
            return None
        page = doc[0]
        images = page.get_images(full=True)
        if len(images) == 1 and not images[0][1]:  # one plain image, no transparency: use it untouched
            info = doc.extract_image(images[0][0])
            if info and (info.get("ext") or "").lower() in PDF_EXTRACTABLE:
                return info["image"]
        return page.get_pixmap(dpi=PDF_COVER_DPI).tobytes("png")


# --------------------------------------------------------------------- the cheap hash
def cover_hash(image: bytes) -> Optional[int]:
    """A 64-bit difference hash (dHash) of an image: each bit says whether a pixel of a 9x8 grey
    thumbnail is brighter than its right neighbour. ``None`` if the image can not be decoded."""
    import pymupdf

    try:
        pixmap = pymupdf.Pixmap(image)
        if pixmap.alpha:
            pixmap = pymupdf.Pixmap(pixmap, 0)
        thumb = pymupdf.Pixmap(pymupdf.Pixmap(pymupdf.csGRAY, pixmap), 9, 8)
        grey = thumb.samples
    except Exception as err:
        logger.debug(f"not an image: {err}")
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (grey[row * 9 + col] > grey[row * 9 + col + 1])
    return bits


def hash_distance(a: int, b: int) -> int:
    """Number of differing bits (0 = same picture, > ``SAME_COVER_DISTANCE`` = probably not)."""
    return (a ^ b).bit_count()


def format_hash(value: int) -> str:
    return f"{value:016x}"


def parse_hash(text: Optional[str]) -> Optional[int]:
    return int(text, 16) if text else None


def path_cover_hash(path: str, fmt: str) -> Optional[int]:
    """The hash of the front cover of a comic file."""
    image = front_cover(path, fmt)
    return cover_hash(image) if image else None


def library_cover_hash(library, path: str) -> Optional[int]:
    """The hash of a registered file's cover: read from the library, or computed and stored there."""
    row = library.get_file(path)
    if row is None:
        return None
    if row.get("cover_hash"):
        return parse_hash(row["cover_hash"])
    value = path_cover_hash(path, row["format"])
    if value is not None:
        library.set_file_fields(path, cover_hash=format_hash(value))
    return value


