"""How sharp a file is (dpi): measured from a few pages, stored, listed by `check --quality`, a filter, and a column in the review screen."""

import io
import zipfile

import pymupdf
import pytest

from bdnex import quality
from bdnex.library import AlbumInfo, Library, SeriesInfo, browse
from bdnex.library.scan import inspect_file


def fake_png(width, height):
    """A picture that is only a header: the measure reads nothing else (generating a real big one takes seconds)."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"")


def real_picture(width, height, kind="jpeg"):
    pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (200,))
    return pixmap.tobytes(kind)


def cbz(path, pages=9, width=1400, height=2000):
    with zipfile.ZipFile(path, "w") as archive:
        for number in range(pages):
            archive.writestr(f"{number:02}.png", fake_png(width, height))
    return str(path)


def pdf(path, pixels=(600, 850)):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_image(page.rect, stream=real_picture(*pixels))
    doc.save(str(path))
    return str(path)


@pytest.mark.parametrize("kind", ["jpeg", "png"])
def test_the_size_of_a_picture_is_read_from_its_header(kind):
    assert quality.image_size(real_picture(300, 420, kind)) == (300, 420)
    assert quality.image_size(fake_png(1500, 2200)) == (1500, 2200)


def test_a_picture_that_is_not_one_has_no_size():
    assert quality.image_size(b"") is None and quality.image_size(b"not a picture at all, just text") is None


def test_a_cbz_is_measured_on_its_page_height(tmp_path):
    assert quality.measure(cbz(tmp_path / "a.cbz", height=2000)) == round(2000 / 11.7)
    assert quality.measure(cbz(tmp_path / "b.cbz", height=2000), height_in=10.0) == 200  # the configured page height


def test_a_pdf_is_measured_on_the_inches_its_picture_covers(tmp_path):
    dpi = quality.measure(pdf(tmp_path / "a.pdf"))
    assert 71 <= dpi <= 75  # 850 pixels over a page 11.7 inches high


def test_a_file_with_nothing_to_measure_has_no_number_and_never_raises(tmp_path):
    empty = tmp_path / "empty.cbz"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("ComicInfo.xml", "<ComicInfo/>")
    broken = tmp_path / "broken.cbz"
    broken.write_bytes(b"PK\x03\x04 not really")
    assert quality.measure(str(empty)) is None and quality.measure(str(broken), "cbz") is None and quality.measure(str(tmp_path / "gone.pdf")) is None


def test_pages_are_taken_from_the_middle_of_the_book():
    assert quality._spread(100)[0] > 0 and quality._spread(100)[-1] < 99 and len(quality._spread(100)) == quality.SAMPLES
    assert quality._spread(1) == [0] and quality._spread(0) == []


def test_the_tiers_and_their_bounds():
    assert [quality.tier_of(n) for n in (None, 0, 119, 120, 179, 180, 259, 260, 600)] == \
        ["not measured", "not measured", "low", "fair", "fair", "good", "good", "high", "high"]
    assert quality.tier_bounds("low") == (0, 120) and quality.tier_bounds("high") == (260, None)


def test_inspecting_a_file_measures_it_and_the_library_stores_it(tmp_path):
    path = cbz(tmp_path / "a.cbz", height=2400)
    info = inspect_file(path)
    assert info.dpi == round(2400 / 11.7)
    with Library(tmp_path / "l.sqlite", root=tmp_path) as library:
        library.add_analysed(info, "md5")
        assert library.get_file(path)["dpi"] == info.dpi
        library.set_dpi(path, 99)
        assert library.get_file(path)["dpi"] == 99


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "comics"
    root.mkdir()
    lib = Library(tmp_path / "l.sqlite", root=root)
    album = lib.add_album(AlbumInfo("b", "1", "Redwin", series=SeriesInfo("b", "n", "Nains"), volume=1))
    made = {}
    for name, height in (("soft.cbz", 900), ("fair.cbz", 1700), ("sharp.cbz", 3300)):
        path = cbz(root / name, pages=4, height=height)
        lib.add_file(path, album)
        made[name] = path
    lib.add_file(pdf(root / "vector.pdf"), album)
    with lib.conn:
        lib.conn.execute("UPDATE files SET dpi = NULL")  # a library from before schema v18: nothing measured yet
    yield lib, root, made
    lib.close()


def test_check_quality_measures_the_files_that_have_no_number_and_leaves_the_others(library, tmp_path, monkeypatch, capsys):
    import yaml

    from bdnex.ui import check_cmd

    lib, root, made = library
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "l.sqlite"), "collection_path": str(root)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    lib.set_dpi(made["fair.cbz"], 150)  # one was measured already
    lib.conn.commit()
    out = io.StringIO()
    assert check_cmd.run(["--quality", "--workers", "1"], out) == 0
    assert "Measuring 3 file(s)" in out.getvalue() and "low" in out.getvalue()
    assert lib.get_file(made["soft.cbz"])["dpi"] == round(900 / 11.7) and lib.get_file(made["sharp.cbz"])["dpi"] == round(3300 / 11.7)
    assert lib.get_file(made["fair.cbz"])["dpi"] == 150  # (not measured again)
    out = io.StringIO()
    assert check_cmd.run(["--quality"], out) == 0 and "already" in out.getvalue()
    out = io.StringIO()
    assert check_cmd.run(["--quality", "--again", "--workers", "1", "soft"], out) == 0 and "Measuring 1 file(s)" in out.getvalue()


def test_the_quality_filter_lists_the_files_by_tier_and_the_query_finds_them(library):
    lib, root, made = library
    for name, dpi in (("soft.cbz", 77), ("fair.cbz", 145), ("sharp.cbz", 282)):
        lib.set_dpi(made[name], dpi)
    from bdnex.config import build_settings

    settings = build_settings({"config_version": 2, "collection_path": str(root)})[0]
    found = dict(browse.facets(lib)["Quality"])
    assert found == {"low": 1, "fair": 1, "good": 0, "high": 1, "not measured": 1}
    assert [r.path for r in browse.rows_for(lib, settings, "facet", ("Quality", "low"))] == [made["soft.cbz"]]
    assert browse.rows_for(lib, settings, "facet", ("Quality", "not measured")).total == 1
    assert [r["path"] for r in lib.query(["dpi:..120"], files=True)] == [made["soft.cbz"]]
