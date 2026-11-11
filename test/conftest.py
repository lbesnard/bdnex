import io
import os
import zipfile

import pytest


def make_image_bytes(color=(200, 30, 30), size=(8, 8)):
    """A tiny valid PNG, built with OpenCV (already a dependency)."""
    import cv2
    import numpy as np

    img = np.full((size[1], size[0], 3), color, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


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
    import cv2
    import numpy as np

    img = np.full((size[1], size[0], 3), color, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


@pytest.fixture
def make_image_pdf(tmp_path):
    """PDF with one embedded JPEG per page; returns (path, [jpeg bytes per page])."""

    def _make(name="scan.pdf", pages=3):
        import pymupdf

        jpegs = [make_jpeg_bytes(color=(40 * i + 10, 90, 160), size=(40 + i, 60)) for i in range(pages)]
        doc = pymupdf.open()
        for jpeg in jpegs:
            page = doc.new_page(width=200, height=300)
            page.insert_image(page.rect, stream=jpeg)
        path = os.path.join(tmp_path, name)
        doc.save(path)
        doc.close()
        return path, jpegs

    return _make
