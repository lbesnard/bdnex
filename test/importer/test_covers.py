"""Taking a cover out of a file, and the cheap cover hash."""

import random
import zipfile

import pymupdf
import pytest

from bdnex.importer import covers
from bdnex.library import Library


def picture(seed, size=(240, 360), blocks=12):
    """A cover-like picture of random coloured blocks, as a pymupdf pixmap."""
    rng = random.Random(seed)
    width, height = size
    cols = [[tuple(rng.randrange(256) for _ in range(3)) for _ in range(blocks)] for _ in range(blocks)]
    data = bytearray()
    for y in range(height):
        row = cols[y * blocks // height]
        for x in range(width):
            data += bytes(row[x * blocks // width])
    return pymupdf.Pixmap(pymupdf.csRGB, width, height, bytes(data), False)


def png(seed, **kw):
    return picture(seed, **kw).tobytes("png")


def small_jpeg(seed):
    """The same picture, smaller and recompressed: what another release of the book would hold."""
    return pymupdf.Pixmap(picture(seed), 120, 180).tobytes("jpeg", jpg_quality=60)


def zipped(path, names):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in names.items():
            archive.writestr(name, data)
    return str(path)


# --------------------------------------------------------------------- front_cover
def test_the_first_page_in_natural_order_is_the_cover(tmp_path):
    path = zipped(tmp_path / "a.cbz", {"page-10.png": b"ten", "page-2.png": b"two", "page-1.png": b"one",
                                       "ComicInfo.xml": b"<x/>"})
    assert covers.front_cover(path, "cbz") == b"one"


def test_junk_entries_are_not_covers(tmp_path):
    path = zipped(tmp_path / "a.cbz", {"__MACOSX/page-0.png": b"junk", ".hidden.png": b"junk", "notes.txt": b"x",
                                       "z/page-5.jpg": b"real"})
    assert covers.front_cover(path, "cbz") == b"real"


def test_no_image_unreadable_or_unknown_means_no_cover(tmp_path):
    assert covers.front_cover(zipped(tmp_path / "e.cbz", {"readme.txt": b"x"}), "cbz") is None
    broken = tmp_path / "b.cbz"
    broken.write_bytes(b"not a zip")
    assert covers.front_cover(str(broken), "cbz") is None
    assert covers.front_cover(str(broken), "unknown") is None
    assert covers.front_cover(str(tmp_path / "missing.cbz"), "cbz") is None


def test_an_empty_page_is_not_a_cover():
    for name in ("test/bd.cbz", "test/bd.cbr"):  # the samples hold zero-byte pages
        assert covers.front_cover(name, name[-3:]) is None


def test_cover_of_a_rar_archive(make_cbz):
    pytest.importorskip("rarfile")
    # rar archives can not be written here: the sample one proves the code path is reached, without a crash
    assert covers.front_cover("test/bd.cbr", "cbr") is None


def test_pdf_cover_is_the_embedded_image_or_a_render(make_image_pdf, make_pdf):
    path, jpegs = make_image_pdf()
    assert covers.front_cover(path, "pdf") == jpegs[0]  # the scanned image, untouched
    assert covers.front_cover(make_pdf(), "pdf").startswith(b"\x89PNG")  # a page without one is rendered


# ------------------------------------------------------------------------- the hash
def test_the_same_picture_in_another_size_and_format_gives_a_near_hash():
    a, b = covers.cover_hash(png(1)), covers.cover_hash(small_jpeg(1))
    assert covers.hash_distance(a, b) <= covers.SAME_COVER_DISTANCE


def test_other_pictures_give_far_hashes():
    distances = [covers.hash_distance(covers.cover_hash(png(1)), covers.cover_hash(png(seed))) for seed in range(2, 12)]
    assert min(distances) > covers.SAME_COVER_DISTANCE


def test_hash_handles_transparency_and_garbage():
    with_alpha = pymupdf.Pixmap(picture(3), 1)  # adds an alpha channel
    assert covers.cover_hash(with_alpha.tobytes("png")) == covers.cover_hash(png(3))
    assert covers.cover_hash(b"not an image") is None
    assert covers.cover_hash(b"") is None


def test_hash_text_round_trip_and_distance():
    value = covers.cover_hash(png(4))
    assert covers.parse_hash(covers.format_hash(value)) == value and len(covers.format_hash(1)) == 16
    assert covers.parse_hash(None) is None and covers.hash_distance(0b1010, 0b0110) == 2


def test_a_registered_files_hash_is_computed_once_and_kept(tmp_path):
    path = zipped(tmp_path / "a.cbz", {"1.png": png(5), "2.png": png(6)})
    library = Library(":memory:")
    assert covers.library_cover_hash(library, path) is None  # not registered
    library.add_file(path)
    value = covers.library_cover_hash(library, path)
    assert value == covers.cover_hash(png(5)) and library.get_file(path)["cover_hash"] == covers.format_hash(value)
    library.set_file_fields(path, cover_hash=covers.format_hash(7))  # what is stored is used, not recomputed
    assert covers.library_cover_hash(library, path) == 7


