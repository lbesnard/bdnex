"""Front covers: take one out of a comic file, and compare two covers.

Two comparisons, for two jobs:

* :func:`sift_similarity` answers "is this the cover of that album?". It compares a file's cover with
  the album's cover found on the web, which differ in size, crop and compression, so it matches local
  features (SIFT). It needs OpenCV (installed with bdnex).
* :func:`cover_hash` / :func:`hash_distance` answer "are these two files the same book?". A 64-bit
  perceptual hash of the cover is tiny, needs no extra dependency and is stored in the library, so
  thousands of files can be compared at once. The same pictures in another container (CBZ, CBR, PDF)
  give almost the same hash.
"""

from __future__ import annotations

import contextlib
import logging
import os
import posixpath
import re
import sys
import tempfile
import threading
import zipfile
from typing import Optional


from bdnex import rartool

from bdnex.organizer.archive import IMAGE_EXTS, PDF_EXTRACTABLE

logger = logging.getLogger(__name__)

def flat_hash(value: int) -> bool:
    """A difference hash with (almost) no bit set, or all set: a blank or uniform page, which says nothing about the book."""
    return not 4 < value.bit_count() < 60


SAME_COVER_DISTANCE = 10  # bits of 64 that may differ between two copies of the same cover
SIFT_HEIGHT = 600  # both images are scaled to this height before the comparison
SIFT_RATIO = 0.6  # Lowe's ratio test: a match counts if it is clearly better than the second best
PDF_COVER_DPI = 100


class CoverUnavailable(RuntimeError):
    """OpenCV is not installed, so covers can not be compared with SIFT."""


class CoverError(ValueError):
    """An image could not be read."""


class TooLittleDetail(CoverError):
    """A picture with too few distinctive points to be compared (a flat or dark cover, a tiny placeholder): the answer is "unknown",
    never "different" (a false alarm on a right match) and never "alike" (a lucky pair of points)."""


MIN_KEYPOINTS = 12  # distinctive points an image needs for its comparison to mean anything


# ------------------------------------------------------------------ taking a cover out
def _natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _is_page(name: str) -> bool:
    base = posixpath.basename(name)
    return (name.lower().endswith(IMAGE_EXTS) and not base.startswith(".")
            and "__macosx" not in name.lower().split("/"))


def _sniff(path: str) -> Optional[str]:
    """``cbz``, ``cbr`` or ``pdf`` from the first bytes of the file; ``None`` if they say nothing."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head.startswith(b"PK"):
        return "cbz"
    if head.startswith(b"Rar!"):
        return "cbr"
    if head.startswith(b"%PDF"):
        return "pdf"
    return None


def front_cover(path: str, fmt: str) -> Optional[bytes]:
    """The bytes of the first page image of a CBZ, CBR or PDF; ``None`` if there is none or the file is unreadable."""
    try:
        fmt = _sniff(path) or fmt  # a ".cbr" that is really a zip is common: trust the content, not the name
        if fmt == "cbz":
            with zipfile.ZipFile(path) as archive:
                names = sorted((i.filename for i in archive.infolist() if not i.is_dir() and _is_page(i.filename)),
                               key=_natural_key)
                return (archive.read(names[0]) or None) if names else None  # an empty page is no cover
        if fmt == "cbr":
            with rartool.RarFile(path) as archive:
                names = sorted((i.filename for i in archive.infolist() if not i.is_dir()
                                and _is_page(i.filename.replace("\\", "/"))), key=_natural_key)
                return (archive.read(names[0]) or None) if names else None
        if fmt == "pdf":
            return _pdf_first_page(path)
    except Exception as err:  # a damaged archive simply has no usable cover
        logger.debug(f"no cover from {path}: {err}")
    return None


def page_image(path: str, fmt: str, index: int = 0) -> tuple[Optional[bytes], int]:
    """``(the picture of page ``index`` (0 is the first), how many pages the file has)``: the review screen's full-size view, to read the credits.
    ``(None, 0)`` when the file cannot be read."""
    try:
        fmt = _sniff(path) or fmt
        if fmt in ("cbz", "cbr"):
            opener = zipfile.ZipFile if fmt == "cbz" else rartool.RarFile
            with opener(path) as archive:
                names = sorted((i.filename for i in archive.infolist() if not i.is_dir() and _is_page(i.filename.replace("\\", "/"))),
                               key=_natural_key)
                if not names:
                    return None, 0
                index = max(0, min(index, len(names) - 1))
                return (archive.read(names[index]) or None), len(names)
        if fmt == "pdf":
            import pymupdf

            with pymupdf.open(path) as doc:
                if doc.page_count == 0:
                    return None, 0
                index = max(0, min(index, doc.page_count - 1))
                return doc[index].get_pixmap(dpi=PDF_COVER_DPI).tobytes("png"), doc.page_count
    except Exception as err:
        logger.debug(f"no page {index} from {path}: {err}")
    return None, 0


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


# -------------------------------------------------------------------- the SIFT check
def _opencv():
    try:
        import cv2
        import numpy
    except ImportError as err:
        raise CoverUnavailable("comparing covers needs OpenCV: pip install opencv-python-headless") from err
    return cv2, numpy


_NATIVE_STDERR = threading.Lock()


@contextlib.contextmanager
def quiet_native_stderr():
    """The JPEG library inside OpenCV writes ``Corrupt JPEG data: 1 extraneous bytes before marker 0xdb`` straight to the process's
    stderr, in the middle of a progress bar, once per picture that has a harmless flaw. Catch what is written while a picture is
    decoded and keep it for ``-vv`` (the picture is decoded anyway)."""
    with _NATIVE_STDERR:
        fd = 2  # the C library's stderr, whatever sys.stderr is
        try:
            saved = os.dup(fd)
        except OSError:  # no stderr at all (a daemon): nothing to catch
            yield
            return
        with tempfile.TemporaryFile() as sink:
            try:
                sys.stderr.flush()
            except (AttributeError, ValueError, OSError):
                pass
            os.dup2(sink.fileno(), fd)
            try:
                yield
            finally:
                os.dup2(saved, fd)
                os.close(saved)
                sink.seek(0)
                text = sink.read().decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug("the image library said: " + " | ".join(sorted(set(text.splitlines()))))


def _grey(cv2, numpy, image: bytes, height: int):
    try:
        with quiet_native_stderr():
            picture = cv2.imdecode(numpy.frombuffer(image, numpy.uint8), cv2.IMREAD_GRAYSCALE) if image else None
    except cv2.error as err:  # OpenCV raises, rather than returns None, for some broken input
        raise CoverError(f"not a readable image: {err}") from err
    if picture is None:
        raise CoverError("not a readable image")
    rows, cols = picture.shape[:2]
    return cv2.resize(picture, (max(1, round(cols * height / rows)), height), interpolation=cv2.INTER_AREA)


def sift_similarity(original: bytes, other: bytes, height: int = SIFT_HEIGHT, ratio: float = SIFT_RATIO) -> float:
    """How alike two covers are, 0-100: the share of the distinctive SIFT features of the image with
    fewer of them that also appear in the other one.

    Raises :class:`CoverUnavailable` without OpenCV and :class:`CoverError` for an unreadable image, or
    :class:`TooLittleDetail` (a CoverError) for one with fewer than :data:`MIN_KEYPOINTS` points: there is nothing to say.
    """
    cv2, numpy = _opencv()
    first, second = _grey(cv2, numpy, original, height), _grey(cv2, numpy, other, height)
    sift = cv2.SIFT_create()
    points_a, described_a = sift.detectAndCompute(first, None)
    points_b, described_b = sift.detectAndCompute(second, None)
    if described_a is None or described_b is None or min(len(described_a), len(described_b)) < MIN_KEYPOINTS:
        raise TooLittleDetail("too little detail in a cover to compare it")
    matcher = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {})  # 1 = KD-tree, for float descriptors
    pairs = matcher.knnMatch(described_a, described_b, k=2)
    good = sum(1 for pair in pairs if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance)
    return min(100.0, 100.0 * good / min(len(points_a), len(points_b)))


def image_extension(data: bytes) -> str:
    """``.jpg``, ``.png``, ``.webp`` or ``.gif`` from the first bytes of an image (``.jpg`` when unknown)."""
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"GIF8"):
        return ".gif"
    return ".jpg"


IMAGE_EXTENSIONS = (".jpg", ".png", ".webp", ".gif")


def _safe(name: str) -> str:
    """A folder or file name made of ``name``. A name that had to be changed gets a short hash, so that two
    different ones never end up alike."""
    import hashlib

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip(".") or "x"
    if cleaned == str(name) and not cleaned.startswith("."):
        return cleaned
    return f"{cleaned}-{hashlib.sha1(str(name).encode('utf-8')).hexdigest()[:8]}"


def cover_folder(source: str, source_id: str) -> str:
    """Where an album's cover lives below ``cover.dir``: ``<source>/<album id>`` (a folder of its own, with room for
    more pictures of the album later). The album id is unique per source, so the folder is too."""
    return f"{_safe(source)}/{_safe(source_id)}"


def save_cover(directory, source: str, source_id: str, data: bytes) -> str:
    """Write a cover below ``directory`` as ``<source>/<album id>/cover.<ext>`` (through a temporary file) and return
    that relative path."""
    import os
    from pathlib import Path

    relative = f"{cover_folder(source, source_id)}/cover{image_extension(data)}"
    target = Path(directory) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    # a name of its own: two threads (or two runs) saving one album's cover must not write, then rename, the same temporary file
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()  # still there only when the write or the rename failed
    return relative


def find_cover(directory, source: str, source_id: str) -> Optional[str]:
    """The relative path of a cover saved earlier for this album, if the file is still there."""
    from pathlib import Path

    folder = cover_folder(source, source_id)
    for extension in IMAGE_EXTENSIONS:
        if (Path(directory) / folder / f"cover{extension}").exists():
            return f"{folder}/cover{extension}"
    return None


def migrate_flat_covers(library, directory) -> int:
    """Move covers saved by earlier versions (``<source>/<id>.<ext>``) into their own folders
    (``<source>/<id>/cover.<ext>``) and point the albums at the new place. Returns how many were moved."""
    import os
    from pathlib import Path

    root = Path(directory)
    if not root.is_dir():
        return 0
    moved = 0
    for source_dir in [p for p in root.iterdir() if p.is_dir()]:
        for old in sorted(source_dir.iterdir()):
            if not old.is_file() or old.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            new_relative = f"{source_dir.name}/{_safe(old.stem)}/cover{old.suffix.lower()}"
            target = root / new_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                old.unlink()  # the new place already has one: the old copy is only in the way
            else:
                os.replace(old, target)
            library.conn.execute("UPDATE albums SET cover_file = ? WHERE cover_file = ?",
                                 (new_relative, f"{source_dir.name}/{old.name}"))
            moved += 1
    library.conn.commit()
    return moved


def opencv_installed() -> bool:
    """Can covers be compared with SIFT (is OpenCV installed)?"""
    try:
        _opencv()
    except CoverUnavailable:
        return False
    return True


MIN_COVER_SIDE = 100  # pixels: smaller is a placeholder, not a cover


MAX_PIXELS = 100_000_000  # a cover is a few hundred thousand pixels; a 30000 x 30000 PNG of a few KB would take gigabytes to decode


def image_size(data: bytes) -> Optional[tuple[int, int]]:
    """``(width, height)`` read from the HEADER of a PNG, JPEG, GIF or WebP (nothing is decoded), ``None`` when it is not one of those or
    the header is cut: used to refuse a decompression bomb before the picture is opened."""
    import struct

    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if kind == b"VP8 ":
                return struct.unpack("<HH", data[26:30])[0] & 0x3FFF, struct.unpack("<HH", data[26:30])[1] & 0x3FFF
            if kind == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if data.startswith(b"\xff\xd8"):
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height, width = struct.unpack(">HH", data[i + 5:i + 9])
                    return width, height
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        return None
    return None


def refuse_bomb(data: bytes) -> None:
    """Raise :class:`CoverError` for a picture whose header says it has more than :data:`MAX_PIXELS` pixels."""
    size = image_size(data)
    if size and size[0] * size[1] > MAX_PIXELS:
        raise CoverError(f"the picture says it is {size[0]}x{size[1]} pixels: not a cover, and not opened")


def check_cover_image(data: bytes) -> None:
    """Raise :class:`CoverError` saying what ``data`` is when it is not a usable cover image (an error page, the
    HTML that wraps an image, a 1-pixel placeholder...)."""
    if not data:
        raise CoverError("nothing was received")
    refuse_bomb(data)
    try:
        import pymupdf

        pixmap = pymupdf.Pixmap(data)
        width, height = pixmap.width, pixmap.height
    except Exception:
        start = data[:60].decode("utf-8", "replace").replace("\n", " ")
        raise CoverError(f"{len(data)} bytes were received, which is not an image (it starts with {start!r})") from None
    if min(width, height) < MIN_COVER_SIDE:
        raise CoverError(f"the picture is only {width}x{height} pixels: a placeholder, not a cover")
