"""Lossless reading and writing of comic archives (CBZ / CBR / PDF -> CBZ)."""

from __future__ import annotations

import hashlib
import os
import posixpath
import zipfile
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
                entries.append(Entry(item.filename, data))
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
