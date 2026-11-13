"""Taking a cover out of a file, the cheap cover hash, and the SIFT comparison."""

import random
import sys
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


# ------------------------------------------------------------------------------ SIFT
cv2 = pytest.importorskip("cv2")
numpy = pytest.importorskip("numpy")


def art(seed, size=(600, 900)):
    """A busy cover with a title: plenty of features to describe."""
    rng = numpy.random.default_rng(seed)
    width, height = size
    image = numpy.full((height, width, 3), 200, numpy.uint8)
    for _ in range(80):
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, width - 40)), int(rng.integers(0, height - 40))
        if rng.random() < 0.5:
            cv2.circle(image, (x, y), int(rng.integers(10, 60)), color, -1)
        else:
            cv2.rectangle(image, (x, y), (x + int(rng.integers(20, 100)), y + int(rng.integers(20, 100))), color, -1)
    cv2.putText(image, f"BDNEX {seed}", (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    return image


def jpeg(image, quality=90):
    return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


def test_the_same_cover_scanned_smaller_scores_high_and_another_one_low():
    original = jpeg(art(1))
    rescanned = jpeg(cv2.resize(art(1), (400, 600)), 60)
    same = covers.sift_similarity(original, rescanned)
    other = covers.sift_similarity(original, jpeg(art(2)))
    assert same > 40 > other and same - other > 25  # the old default threshold, 40, separates them


def test_comparison_is_symmetric_enough_and_bounded():
    a, b = jpeg(art(3)), jpeg(art(3), 50)
    assert 0 <= covers.sift_similarity(a, b) <= 100 and abs(covers.sift_similarity(a, b) - covers.sift_similarity(b, a)) < 15


def test_a_flat_image_has_nothing_to_compare():
    assert covers.sift_similarity(jpeg(art(1)), jpeg(numpy.full((900, 600, 3), 128, numpy.uint8))) == 0.0


def test_an_unreadable_image_is_an_error_not_a_crash():
    with pytest.raises(covers.CoverError):
        covers.sift_similarity(b"not an image", jpeg(art(1)))
    with pytest.raises(covers.CoverError):
        covers.sift_similarity(jpeg(art(1)), b"")


def test_without_opencv_the_error_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    with pytest.raises(covers.CoverUnavailable, match="bdnex\\[cover\\]"):
        covers.sift_similarity(b"x", b"y")


def test_a_cover_is_found_whatever_the_extension_says(tmp_path):
    import shutil
    import zipfile
    zipped = tmp_path / "really-a-zip.cbr"
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.writestr("01.JPG", png(2))
    assert covers.front_cover(str(zipped), "cbr") == png(2)
    shutil.copy(zipped, tmp_path / "x.pdf")
    assert covers.front_cover(str(tmp_path / "x.pdf"), "pdf") == png(2)


def test_only_a_real_picture_passes_for_a_cover():
    import pytest
    from bdnex.importer import covers
    covers.check_cover_image(jpeg(art(1)))
    html = b'<html><head><meta name="viewport" content="width=device-width"></head><body><img src="x.jpg"></body></html>'
    with pytest.raises(covers.CoverError, match="not an image.*<html>"):
        covers.check_cover_image(html)
    with pytest.raises(covers.CoverError, match="nothing"):
        covers.check_cover_image(b"")
