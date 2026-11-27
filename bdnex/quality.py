"""How sharp a comic file is: its resolution in dots per inch (dpi), worked out from a few of its pages.

* A **PDF** says how big each picture is on the page, so its dpi is real: the picture's pixels over its printed inches.
* A **CBZ / CBR** has pictures and no printed size, so the dpi is an *estimate*: the pixel height of a page over a page of
  ``quality.page_height_in`` inches (11.7 = an A4 page; an album is about 11.5 to 12.2).

Only a few pages are looked at (spread through the book, not the first or the last), and the median is kept, so a file costs a few
milliseconds, not a read of every page. Nothing here needs a library: ``measure(path)`` reads the file and returns the number.
"""

from __future__ import annotations

import logging
import struct
from statistics import median
from typing import Optional

logger = logging.getLogger(__name__)

SAMPLES = 5  # pages looked at
DEFAULT_PAGE_HEIGHT_IN = 11.7
page_height_in = DEFAULT_PAGE_HEIGHT_IN  # `quality.page_height_in` of the configuration (``configure``): what a registered file is measured with
# (name, lowest dpi, what it means): a rough guide (print is 300; a good scan to read on a screen 150-250; soft under 120)
TIERS = (("low", 0, "under 120 dpi: soft, worth finding again"), ("fair", 120, "120 to 180 dpi"),
         ("good", 180, "180 to 260 dpi"), ("high", 260, "260 dpi and more"))
NOT_MEASURED = "not measured"


def tier_of(dpi: Optional[int]) -> str:
    """The tier name of a dpi (``not measured`` when there is none)."""
    if not dpi:
        return NOT_MEASURED
    return [name for name, low, _ in TIERS if dpi >= low][-1]


def tier_bounds(name: str) -> tuple[int, Optional[int]]:
    """``(from, to)`` dpi of a tier: ``to`` is ``None`` for the last one. ``from`` is inclusive, ``to`` exclusive."""
    for index, (tier, low, _) in enumerate(TIERS):
        if tier == name:
            return low, (TIERS[index + 1][1] if index + 1 < len(TIERS) else None)
    raise ValueError(f"no tier {name!r}")


def describe(dpi: Optional[int], estimated: bool = True) -> str:
    return "not measured" if not dpi else f"{'~' if estimated else ''}{dpi} dpi"


def image_size(data: bytes) -> Optional[tuple[int, int]]:
    """``(width, height)`` in pixels of a JPEG, PNG, GIF, BMP or WebP picture, read from its header (nothing is decoded)."""
    try:
        if data.startswith(b"\xff\xd8"):  # JPEG: walk the segments to the first start-of-frame
            at = 2
            while at + 9 < len(data):
                if data[at] != 0xFF:
                    at += 1
                    continue
                marker = data[at + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                    at += 1 if marker == 0xFF else 2
                    continue
                length = struct.unpack(">H", data[at + 2:at + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", data[at + 5:at + 9])
                    return width, height
                at += 2 + length
            return None
        if data.startswith(b"\x89PNG"):
            width, height = struct.unpack(">II", data[16:24])
            return width, height
        if data[:6] in (b"GIF87a", b"GIF89a"):
            width, height = struct.unpack("<HH", data[6:10])
            return width, height
        if data.startswith(b"BM"):
            width, height = struct.unpack("<ii", data[18:26])
            return width, abs(height)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if kind == b"VP8 ":
                width, height = struct.unpack("<HH", data[26:30])
                return width & 0x3FFF, height & 0x3FFF
            if kind == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except (struct.error, IndexError):
        return None
    return None


def _spread(count: int, samples: int = SAMPLES) -> list[int]:
    """Indexes spread through ``count`` pages, leaving out the first and the last (a cover and a back cover are often another size)."""
    if count <= 0:
        return []
    if count <= 2 or count <= samples:
        return list(range(count))
    return sorted({round((k + 1) * (count - 1) / (samples + 1)) for k in range(samples)} - {0, count - 1}) or [count // 2]


def dpi_of_height(pixels: int, page_height_in: float = DEFAULT_PAGE_HEIGHT_IN) -> int:
    return round(pixels / page_height_in)


def _archive_dpi(read_image, names: list[str], page_height_in: float) -> Optional[int]:
    from bdnex.library.scan import IMAGE_EXTENSIONS

    pages = sorted(name for name in names if name.lower().endswith(IMAGE_EXTENSIONS))
    heights = []
    for index in _spread(len(pages)):
        size = image_size(read_image(pages[index]))
        if size:
            heights.append(size[1])
    return dpi_of_height(int(median(heights)), page_height_in) if heights else None


def _pdf_dpi(doc) -> Optional[int]:
    """The sharpness of the pictures of a few pages, as the PDF conversion reads it (``archive._source_dpi``: a scan is layers, a colour picture under
    line art under thin strips: the colour picture counts). A PDF drawn with vectors has no pictures: no dpi."""
    from bdnex.organizer.archive import _source_dpi

    found = [dpi for dpi in (_source_dpi(doc[index]) for index in _spread(doc.page_count)) if dpi]
    return round(median(found)) if found else None


def configure(height_in: float) -> None:
    """Set the page height the files are measured against from now on (the configuration's ``quality.page_height_in``)."""
    global page_height_in
    page_height_in = float(height_in)


def measure(path: str, fmt: Optional[str] = None, height_in: Optional[float] = None) -> Optional[int]:
    """The dpi of a comic file, or ``None`` when it cannot be told (no pictures, an unknown format, an unreadable file). Never raises."""
    from bdnex.library.scan import detect_format

    page_height_in = height_in or globals()["page_height_in"]
    try:
        fmt = fmt or detect_format(path)
        if fmt == "cbz":
            import zipfile

            with zipfile.ZipFile(path) as archive:
                return _archive_dpi(archive.read, archive.namelist(), page_height_in)
        if fmt == "cbr":
            from bdnex import rartool

            with rartool.RarFile(path) as archive:
                return _archive_dpi(archive.read, archive.namelist(), page_height_in)
        if fmt == "pdf":
            import pymupdf

            with pymupdf.open(path) as doc:
                return _pdf_dpi(doc)
    except Exception as err:  # a broken file is for `bdnex check`; here it is just not measured
        logger.debug("%s: the resolution could not be measured: %s", path, err)
    return None
