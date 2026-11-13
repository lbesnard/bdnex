import io
import os
import zipfile

import pytest


def _solid(color, size, fmt):
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, *size), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes(fmt)


def make_image_bytes(color=(200, 30, 30), size=(8, 8)):
    """A tiny valid PNG (PyMuPDF is already a dependency)."""
    return _solid(color, size, "png")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """No test may read or write the real home folder: `~` (library, pages, trash...) is a temporary one."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("BDNEX_CONFIG", raising=False)


@pytest.fixture(autouse=True)
def no_automatic_cover_check(monkeypatch):
    """`cover.check: auto` must not depend on whether OpenCV happens to be installed: a test that wants the check says so."""
    monkeypatch.setattr("bdnex.importer.covers.opencv_installed", lambda: False)


@pytest.fixture
def make_cbz(tmp_path):
    """Factory creating a fake CBZ with N pages (and optional ComicInfo.xml)."""

    def _make(name="album.cbz", pages=3, comicinfo=False, directory=None):
        directory = directory or tmp_path
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with zipfile.ZipFile(path, "w") as zf:
            for i in range(pages):
                zf.writestr(f"page{i:03d}.png", make_image_bytes(color=(10 * i, 50, 90)))
            if comicinfo:
                zf.writestr("ComicInfo.xml", "<ComicInfo><Title>x</Title></ComicInfo>")
        return str(path)

    return _make


@pytest.fixture
def make_pdf(tmp_path):
    """Factory creating a fake PDF with N blank pages (via PyMuPDF)."""

    def _make(name="album.pdf", pages=2):
        import pymupdf

        path = os.path.join(tmp_path, name)
        doc = pymupdf.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
        doc.close()
        return path

    return _make


def make_jpeg_bytes(color=(30, 120, 200), size=(40, 60)):
    return _solid(color, size, "jpeg")


@pytest.fixture
def make_image_pdf(tmp_path):
    """PDF with one embedded JPEG per page; returns (path, [jpeg bytes per page])."""

    def _make(name="scan.pdf", pages=3):
        import pymupdf

        jpegs = [make_jpeg_bytes(color=((40 * i + 10) % 256, 90, 160), size=(40 + i, 60)) for i in range(pages)]
        doc = pymupdf.open()
        for jpeg in jpegs:
            page = doc.new_page(width=200, height=300)
            page.insert_image(page.rect, stream=jpeg)
        path = os.path.join(tmp_path, name)
        doc.save(path)
        doc.close()
        return path, jpegs

    return _make


def noisy_jpeg(seed, size=(60, 80)):
    """A deterministic, incompressible JPEG: a flipped byte inside it still decodes (with artefacts)."""
    import random

    import pymupdf

    rng = random.Random(seed)
    raw = bytes(rng.randrange(256) for _ in range(size[0] * size[1] * 3))
    return pymupdf.Pixmap(pymupdf.csRGB, size[0], size[1], raw, False).tobytes("jpeg")


@pytest.fixture
def make_damaged_cbz(tmp_path):
    """CBZ whose page ``bad_page`` fails its checksum.

    damage="crc":  the data is intact in size and still decodes, only the checksum is wrong (repairable).
    damage="junk": the page is not an image at all (not repairable).
    Returns (path, {member name: original bytes}).
    """
    import struct
    import zipfile

    def _make(name="damaged.cbz", pages=4, bad_page=1, damage="crc", comicinfo=None):
        path = os.path.join(tmp_path, name)
        originals = {}
        with zipfile.ZipFile(path, "w") as zf:
            if comicinfo:
                zf.writestr("ComicInfo.xml", comicinfo)
            for i in range(pages):
                data = noisy_jpeg(i)
                if i == bad_page and damage == "junk":
                    data = b"this is definitely not a picture " * 500
                originals[f"p{i:02d}.jpg"] = data
                zf.writestr(f"p{i:02d}.jpg", data, compress_type=zipfile.ZIP_STORED)
        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo(f"p{bad_page:02d}.jpg")
        with open(path, "r+b") as fh:
            fh.seek(info.header_offset + 26)
            name_len, extra_len = struct.unpack("<HH", fh.read(4))
            at = info.header_offset + 30 + name_len + extra_len + info.file_size // 2
            fh.seek(at)
            byte = fh.read(1)
            fh.seek(at)
            fh.write(bytes([byte[0] ^ 0xFF]))  # wrong checksum, same size
        return path, originals

    return _make
