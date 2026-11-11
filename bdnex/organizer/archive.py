"""Lossless reading and writing of comic archives (CBZ / CBR / PDF -> CBZ)."""

from __future__ import annotations

import hashlib
import os
import posixpath
import zipfile
import zlib
from dataclasses import dataclass
from typing import Optional

import rarfile

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
STORED_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")  # already compressed: do not deflate again
PDF_EXTRACTABLE = {"jpg", "jpeg", "png", "gif", "bmp", "webp"}
COMICINFO = "comicinfo.xml"


class ArchiveError(Exception):
    """The archive can not be read, converted or verified."""


@dataclass
class Entry:
    name: str
    data: bytes
    compress_type: Optional[int] = None  # kept from the source archive; None = decide from the extension

    @property
    def is_image(self) -> bool:
        return self.name.lower().endswith(IMAGE_EXTS)


def _is_comicinfo(name: str) -> bool:
    return posixpath.basename(name).lower() == COMICINFO


def read_entries(path: str, fmt: str, pdf_mode: str = "auto", pdf_dpi: int = 200) -> tuple[list[Entry], Optional[bytes]]:
    """Return (entries, existing ComicInfo.xml bytes) of an archive; ComicInfo is not in ``entries``."""
    try:
        if fmt == "cbz":
            return _read_zip(path)
        if fmt == "cbr":
            return _read_rar(path)
        if fmt == "pdf":
            return _read_pdf(path, pdf_mode, pdf_dpi), None
    except ArchiveError:
        raise
    except Exception as err:
        raise ArchiveError(f"{os.path.basename(path)}: {err}") from err
    raise ArchiveError(f"unsupported format {fmt!r}")


def _read_zip(path):
    entries, info = [], None
    with zipfile.ZipFile(path) as zf:
        for item in zf.infolist():
            if item.is_dir():
                continue
            data = zf.read(item)
            if _is_comicinfo(item.filename):
                info = info or data
            else:
                entries.append(Entry(item.filename, data, item.compress_type))
    return entries, info


def _read_rar(path):
    entries, info = [], None
    with rarfile.RarFile(path) as rf:
        for item in rf.infolist():
            if item.is_dir():
                continue
            data = rf.read(item)
            if _is_comicinfo(item.filename):
                info = info or data
            else:
                entries.append(Entry(item.filename.replace("\\", "/"), data))
    return entries, info


def _read_pdf(path, mode: str, dpi: int) -> list[Entry]:
    import pymupdf

    with pymupdf.open(path) as doc:
        if doc.page_count == 0:
            raise ArchiveError("the PDF has no pages")
        if mode in ("auto", "extract"):
            extracted = _pdf_extract(doc)
            if extracted is not None:
                return extracted
            if mode == "extract":
                raise ArchiveError("some pages are not a single plain image: use pdf_mode auto or render")
        return _pdf_render(doc, dpi)


def _pdf_extract(doc) -> Optional[list[Entry]]:
    """The embedded page images, untouched, or None if any page is not one plain image."""
    entries = []
    width = len(str(doc.page_count))
    for number, page in enumerate(doc, start=1):
        images = page.get_images(full=True)
        if len(images) != 1:
            return None
        xref, smask = images[0][0], images[0][1]
        if smask:  # transparency: the bare image would not look like the page
            return None
        info = doc.extract_image(xref)
        ext = (info.get("ext") or "").lower()
        if not info or ext not in PDF_EXTRACTABLE:
            return None
        entries.append(Entry(f"page-{number:0{width}d}.{'jpg' if ext == 'jpeg' else ext}", info["image"]))
    return entries


def _pdf_render(doc, dpi: int) -> list[Entry]:
    width = len(str(doc.page_count))
    return [Entry(f"page-{n:0{width}d}.png", page.get_pixmap(dpi=dpi).tobytes("png"))
            for n, page in enumerate(doc, start=1)]


def page_count(entries: list[Entry]) -> int:
    return sum(1 for e in entries if e.is_image)


def write_cbz(dest: str, entries: list[Entry], comicinfo: Optional[bytes]) -> None:
    """Write a CBZ with ``ComicInfo.xml`` at its root (entries are stored byte for byte)."""
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with zipfile.ZipFile(dest, "w") as zf:
        if comicinfo is not None:
            zf.writestr("ComicInfo.xml", comicinfo, compress_type=zipfile.ZIP_DEFLATED)
        for entry in entries:
            kind = entry.compress_type
            if kind not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):  # unknown or from another format
                kind = zipfile.ZIP_STORED if entry.name.lower().endswith(STORED_EXTS) else zipfile.ZIP_DEFLATED
            zf.writestr(entry.name, entry.data, compress_type=kind)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_cbz(dest: str, entries: list[Entry], comicinfo: Optional[bytes]) -> None:
    """Re-open ``dest`` and check it holds exactly ``entries`` (and the ComicInfo)."""
    try:
        with zipfile.ZipFile(dest) as zf:
            bad = zf.testzip()
            if bad:
                raise ArchiveError(f"corrupted member {bad} in the new archive")
            found, info = {}, None
            for item in zf.infolist():
                if item.is_dir():
                    continue
                data = zf.read(item)
                if _is_comicinfo(item.filename):
                    info = data
                else:
                    found[item.filename] = _digest(data)
    except zipfile.BadZipFile as err:
        raise ArchiveError(f"the new archive is not a valid zip: {err}") from err
    expected = {e.name: _digest(e.data) for e in entries}
    if found != expected:
        raise ArchiveError("the new archive differs from the source (page count or bytes)")
    if comicinfo is not None and info != comicinfo:
        raise ArchiveError("ComicInfo.xml was not written correctly")


# ------------------------------------------------------------------ repairing
@dataclass
class LenientRead:
    """Everything that could be read from a damaged archive."""

    entries: list[Entry]
    comicinfo: Optional[bytes]
    repaired: list[str]  # members whose checksum was wrong but whose data is complete and decodable
    lost: list[str]  # members that could not be recovered (with the reason)

    @property
    def ok(self) -> bool:
        return not self.lost


DECODABLE = (".jpg", ".jpeg", ".png", ".gif", ".bmp")


def _decodes(name: str, data: bytes) -> bool:
    """True if ``data`` is a readable image (images only; other members are accepted)."""
    if not name.lower().endswith(DECODABLE):
        return True
    try:
        import pymupdf

        pymupdf.Pixmap(data)
        return True
    except Exception:
        return False


def _read_ignoring_crc(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    with zf.open(info) as handle:
        handle._expected_crc = None  # zipfile skips the CRC test when there is no reference value
        return handle.read()


def read_entries_lenient(path: str, fmt: str) -> LenientRead:
    """Read an archive, keeping members whose only fault is a wrong checksum.

    A member is *repaired* when it decompresses to its declared size and, for images, decodes. Anything
    else is *lost*. The caller decides what to do when something is lost; nothing is written here.
    """
    if fmt == "cbz":
        return _lenient_zip(path)
    if fmt == "cbr":
        return _lenient_rar(path)
    return LenientRead([], None, [], [f"{fmt}: nothing to repair here"])


def _lenient_zip(path: str) -> LenientRead:
    result = LenientRead([], None, [], [])
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            repaired = False
            try:
                data = zf.read(info)
            except zipfile.BadZipFile as err:
                if "CRC" not in str(err):
                    result.lost.append(f"{info.filename}: {err}")
                    continue
                try:
                    data = _read_ignoring_crc(zf, info)
                except (zlib.error, EOFError, zipfile.BadZipFile, NotImplementedError) as err2:
                    result.lost.append(f"{info.filename}: data damaged ({err2})")
                    continue
                if len(data) != info.file_size or not _decodes(info.filename, data):
                    result.lost.append(f"{info.filename}: data damaged beyond the checksum")
                    continue
                repaired = True
            except (zlib.error, EOFError, NotImplementedError, RuntimeError) as err:
                result.lost.append(f"{info.filename}: data damaged ({err})")
                continue
            if _is_comicinfo(info.filename):
                result.comicinfo = result.comicinfo or data
            else:
                result.entries.append(Entry(info.filename, data, info.compress_type))
                if repaired:
                    result.repaired.append(info.filename)
    return result


def _lenient_rar(path: str) -> LenientRead:
    result = LenientRead([], None, [], [])
    with rarfile.RarFile(path) as rf:
        for info in rf.infolist():
            if info.is_dir():
                continue
            try:
                data = rf.read(info)
            except Exception as err:
                result.lost.append(f"{info.filename}: {err}")
                continue
            if _is_comicinfo(info.filename):
                result.comicinfo = result.comicinfo or data
            else:
                result.entries.append(Entry(info.filename.replace("\\", "/"), data))
    return result
