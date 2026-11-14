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
    rendered: bool = False  # a PDF page that had to be drawn (not an image taken out untouched)
    decoded: bool = False  # a PDF picture in a format viewers do not open, saved as PNG pixel for pixel

    @property
    def is_image(self) -> bool:
        return self.name.lower().endswith(IMAGE_EXTS)


def _is_comicinfo(name: str) -> bool:
    return posixpath.basename(name).lower() == COMICINFO


def read_entries(path: str, fmt: str, pdf_mode: str = "auto", pdf_dpi: int = 200,
                 pdf_render_format: str = "auto", pdf_jpeg_quality: int = 90) -> tuple[list[Entry], Optional[bytes]]:
    """Return (entries, existing ComicInfo.xml bytes) of an archive; ComicInfo is not in ``entries``."""
    try:
        if fmt == "cbz":
            return _read_zip(path)
        if fmt == "cbr":
            return _read_rar(path)
        if fmt == "pdf":
            return _read_pdf(path, pdf_mode, pdf_dpi, pdf_render_format, pdf_jpeg_quality), None
    except ArchiveError:
        raise
    except Exception as err:
        raise ArchiveError(f"{os.path.basename(path)}: {err}") from err
    raise ArchiveError(f"unsupported format {fmt!r}")


def peek(path: str, fmt: str) -> tuple[Optional[bytes], Optional[int]]:
    """(existing ComicInfo.xml, number of pages) of a CBZ without reading its pictures: what a preview needs.

    CBR and PDF are always converted, so a preview has nothing to ask of them: ``(None, None)``."""
    if fmt != "cbz":
        return None, None
    info, pages = None, 0
    with zipfile.ZipFile(path) as zf:
        for item in zf.infolist():
            if item.is_dir():
                continue
            if _is_comicinfo(item.filename):
                info = info or zf.read(item)
            elif item.filename.lower().endswith(IMAGE_EXTS):
                pages += 1
    return info, pages


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


MAIN_IMAGE_SHARE = 0.85  # an image covering this much of the page is the page
LEFT_OUT_SHARE = 0.02  # other images smaller than this (a logo, a stamp) may be left out of the extraction
JPEG_QUALITY = 90


def _read_pdf(path, mode: str, dpi: int, render_format: str = "auto", jpeg_quality: int = JPEG_QUALITY) -> list[Entry]:
    """The pages of a PDF as images, page by page: each page that is one plain image is taken out
    untouched (lossless); the others are drawn, unless ``mode`` is ``extract`` (then they are an error)."""
    import pymupdf

    with pymupdf.open(path) as doc:
        if doc.page_count == 0:
            raise ArchiveError("the PDF has no pages")
        width = len(str(doc.page_count))
        entries, odd = [], []
        for number, page in enumerate(doc, start=1):
            taken = None if mode == "render" else _pdf_page_image(doc, page)
            if taken is not None:
                ext, data, decoded = taken
                entries.append(Entry(f"page-{number:0{width}d}.{ext}", data, decoded=decoded))
                continue
            if mode == "extract":
                odd.append(number)
                continue
            ext, data = _pdf_render_page(page, dpi, render_format, jpeg_quality)
            entries.append(Entry(f"page-{number:0{width}d}.{ext}", data, rendered=True))
        if odd:
            raise ArchiveError(f"page(s) {', '.join(map(str, odd[:10]))}{'...' if len(odd) > 10 else ''} "
                               "are not a single plain image: use pdf_mode auto or render")
        return entries


def _pdf_page_image(doc, page) -> Optional[tuple[str, bytes, bool]]:
    """``(extension, bytes, decoded)`` of the image that is this page; ``None`` if the page is anything else
    (text, several pictures, transparency). The picture is untouched, except in a format a viewer would not
    open (JPEG 2000, JBIG2...): that one is decoded and saved as PNG, which keeps every pixel (``decoded``)."""
    images = page.get_images(full=True)
    if not images:
        return None
    if len(images) > 1:
        area = abs(page.rect.width * page.rect.height) or 1.0
        shares = {}
        for info in page.get_image_info(xrefs=True):
            box = info["bbox"]
            shares[info["xref"]] = max(0.0, (box[2] - box[0]) * (box[3] - box[1])) / area
        main = [im for im in images if shares.get(im[0], 0) >= MAIN_IMAGE_SHARE]
        others = [im for im in images if im not in main]
        if len(main) != 1 or any(shares.get(im[0], 1) >= LEFT_OUT_SHARE for im in others):
            return None
        images = main
    xref, smask = images[0][0], images[0][1]
    if smask:  # transparency: the bare image would not look like the page
        return None
    info = doc.extract_image(xref)
    ext = ((info or {}).get("ext") or "").lower()
    if info and ext in PDF_EXTRACTABLE:
        return ("jpg" if ext == "jpeg" else ext), info["image"], False
    return _decode_picture(doc, xref)


def _decode_picture(doc, xref) -> Optional[tuple[str, bytes, bool]]:
    """A picture in an unusual format as a PNG with the same pixels (grey or RGB); ``None`` if it can not be."""
    import pymupdf

    try:
        pixmap = pymupdf.Pixmap(doc, xref)
        if pixmap.alpha or pixmap.colorspace is None or pixmap.colorspace.n not in (1, 3):
            return None  # a colour space change would not keep the pixels: draw the page instead
        return "png", pixmap.tobytes("png"), True
    except Exception:
        return None


def _is_photographic(images) -> bool:
    """A page with a JPEG, or any picture of more than one bit that is not a palette, has tones: a PNG of it is huge.
    Pages of 1-bit scans, palette pictures or plain vector art have none and are best as PNG."""
    return any("DCT" in str(im[8]) or (im[4] > 1 and im[5] != "Indexed") for im in images)


def _source_dpi(page) -> Optional[float]:
    """The resolution of the sharpest picture on the page (its pixels per inch), ``None`` if there is none."""
    best = 0.0
    for info in page.get_image_info():
        inches = abs(info["bbox"][2] - info["bbox"][0]) / 72
        if inches > 0:
            best = max(best, info["width"] / inches)
    return best or None


def _pdf_render_page(page, dpi: int, render_format: str, jpeg_quality: int = JPEG_QUALITY) -> tuple[str, bytes]:
    """A page drawn as an image. ``auto``: JPEG when the page holds photographic pictures (a PNG of those is many
    times bigger for the same look), PNG for anything else (1-bit scans, text, vector art). Never drawn at a higher
    resolution than its pictures have: that only makes the file bigger."""
    if render_format == "auto":
        render_format = "jpeg" if _is_photographic(page.get_images(full=True)) else "png"
    source = _source_dpi(page)
    pixmap = page.get_pixmap(dpi=min(dpi, round(source)) if source and source >= 72 else dpi)
    if render_format == "jpeg":
        if pixmap.alpha:
            import pymupdf
            pixmap = pymupdf.Pixmap(pixmap, 0)
        return "jpg", pixmap.tobytes("jpeg", jpg_quality=jpeg_quality)
    return "png", pixmap.tobytes("png")


# ------------------------------------------------------------------ PDF tagged in place (write.pdf: tag)
PDF_COMICINFO = "ComicInfo.xml"  # the name of the file attached to a tagged PDF


def pdf_comicinfo(path: str) -> tuple[Optional[bytes], int]:
    """``(the ComicInfo.xml attached to a PDF, its number of pages)``. The pictures are not read."""
    import pymupdf

    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass or doc.is_encrypted:
                raise ArchiveError("the PDF is encrypted")
            if doc.is_repaired:
                raise ArchiveError("the PDF's structure is damaged")
            info = doc.embfile_get(PDF_COMICINFO) if PDF_COMICINFO in doc.embfile_names() else None
            return info, doc.page_count
    except ArchiveError:
        raise
    except Exception as err:
        raise ArchiveError(f"{os.path.basename(path)}: {err}") from err


def _pdf_title(fields: dict) -> str:
    number = fields.get("Number")
    return " - ".join(part for part in (fields.get("Series"), f"T{number}" if number else "", fields.get("Title")) if part)


def pdf_info(fields: dict) -> dict:
    """The classic Info dictionary entries (what every PDF viewer shows) for these ComicInfo fields."""
    info = {"title": _pdf_title(fields), "author": fields.get("Writer", ""), "subject": fields.get("Summary", "")[:1000],
            "keywords": fields.get("Genre", "")}
    return {key: value for key, value in info.items() if value}


def pdf_xmp(fields: dict) -> str:
    """An XMP packet: Dublin Core (title, creator, description, publisher, subject, language, date) and the
    series and its number the way Calibre keeps them."""
    from xml.sax.saxutils import escape

    def alt(value):
        return f'<rdf:Alt><rdf:li xml:lang="x-default">{escape(value)}</rdf:li></rdf:Alt>'

    def many(kind, text):
        items = "".join(f"<rdf:li>{escape(item.strip())}</rdf:li>" for item in text.split(",") if item.strip())
        return f"<rdf:{kind}>{items}</rdf:{kind}>"

    parts = []
    if _pdf_title(fields):
        parts.append(f"<dc:title>{alt(_pdf_title(fields))}</dc:title>")
    if fields.get("Writer"):
        parts.append(f"<dc:creator>{many('Seq', fields['Writer'])}</dc:creator>")
    if fields.get("Summary"):
        parts.append(f"<dc:description>{alt(fields['Summary'])}</dc:description>")
    if fields.get("Publisher"):
        parts.append(f"<dc:publisher>{many('Bag', fields['Publisher'])}</dc:publisher>")
    if fields.get("Genre"):
        parts.append(f"<dc:subject>{many('Bag', fields['Genre'])}</dc:subject>")
    if fields.get("LanguageISO"):
        parts.append(f"<dc:language>{many('Bag', fields['LanguageISO'])}</dc:language>")
    if fields.get("Year"):
        parts.append(f"<dc:date><rdf:Seq><rdf:li>{escape(str(fields['Year']))}</rdf:li></rdf:Seq></dc:date>")
    if fields.get("Series"):
        index = f"<calibreSI:series_index>{escape(str(fields['Number']))}</calibreSI:series_index>" if fields.get("Number") else ""
        parts.append(f'<calibre:series rdf:parseType="Resource"><rdf:value>{escape(fields["Series"])}</rdf:value>{index}</calibre:series>')
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:calibre="http://calibre-ebook.com/xmp-namespace" '
            'xmlns:calibreSI="http://calibre-ebook.com/xmp-namespace-series-index">' + "".join(parts) +
            "</rdf:Description></rdf:RDF></x:xmpmeta>")


def tag_pdf(path: str, xml: bytes, fields: dict) -> None:
    """Write the tags into the PDF ``path`` (a copy: it is changed in place) with an incremental save: the original
    bytes stay as they are and the changes are appended. Raises :class:`ArchiveError` when that is not possible."""
    import pymupdf

    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass or doc.is_encrypted:
                raise ArchiveError("the PDF is encrypted: it can not be tagged")
            if doc.is_repaired:
                raise ArchiveError("the PDF's structure is damaged: it can not be tagged in place")
            info = {key: value for key, value in (doc.metadata or {}).items() if key not in ("format", "encryption") and value}
            info.update(pdf_info(fields))
            doc.set_metadata(info)
            doc.set_xml_metadata(pdf_xmp(fields))
            if PDF_COMICINFO in doc.embfile_names():
                doc.embfile_upd(PDF_COMICINFO, xml)
            else:
                doc.embfile_add(PDF_COMICINFO, xml, filename=PDF_COMICINFO, desc="Comic book metadata (bdnex)")
            doc.saveIncr()
    except ArchiveError:
        raise
    except Exception as err:
        raise ArchiveError(f"{os.path.basename(path)} could not be tagged: {err}") from err


def verify_tagged_pdf(tagged: str, original: str, xml: bytes) -> None:
    """The tagged copy begins with exactly the original's bytes (so the pages are untouched), has as many pages and
    holds the ComicInfo.xml."""
    size = os.path.getsize(original)
    if os.path.getsize(tagged) < size:
        raise ArchiveError("the tagged PDF is shorter than the original")
    with open(original, "rb") as before, open(tagged, "rb") as after:
        while True:
            chunk = before.read(1 << 20)
            if not chunk:
                break
            if after.read(len(chunk)) != chunk:
                raise ArchiveError("the tagged PDF does not begin with the original's bytes")
    found, pages = pdf_comicinfo(tagged)
    if found != xml:
        raise ArchiveError("the ComicInfo.xml in the tagged PDF is not the one written")
    if pages != pdf_comicinfo(original)[1]:
        raise ArchiveError("the tagged PDF has another number of pages")


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
