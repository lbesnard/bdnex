import os
import zipfile

import pytest

from bdnex.organizer import archive
from bdnex.organizer.archive import ArchiveError, Entry

BD_CBR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bd.cbr")
BD_CBZ = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bd.cbz")


def test_read_cbz_separates_comicinfo_even_in_subfolder():
    entries, info = archive.read_entries(BD_CBZ, "cbz")  # sample has bd/ComicInfo.xml
    assert info is not None and info == b""
    assert sorted(e.name for e in entries) == ["bd/a.jpg", "bd/b.jpg", "bd/c.gif"]


def test_read_cbr_real_sample():
    entries, info = archive.read_entries(BD_CBR, "cbr")
    assert archive.page_count(entries) == 3


def test_write_cbz_puts_comicinfo_at_root_and_verifies(tmp_path):
    entries = [Entry("p1.jpg", b"one"), Entry("sub/p2.png", b"two"), Entry("readme.txt", b"txt")]
    dest = str(tmp_path / "x" / "out.cbz")
    archive.write_cbz(dest, entries, b"<ComicInfo/>")
    with zipfile.ZipFile(dest) as zf:
        assert zf.namelist()[0] == "ComicInfo.xml"
        assert zf.getinfo("p1.jpg").compress_type == zipfile.ZIP_STORED  # images are not deflated again
        assert zf.getinfo("readme.txt").compress_type == zipfile.ZIP_DEFLATED
    archive.verify_cbz(dest, entries, b"<ComicInfo/>")


def test_verify_detects_changed_missing_extra_and_bad_xml(tmp_path):
    entries = [Entry("a.jpg", b"1"), Entry("b.jpg", b"2")]
    dest = str(tmp_path / "o.cbz")
    archive.write_cbz(dest, entries, b"<x/>")
    with pytest.raises(ArchiveError):
        archive.verify_cbz(dest, [Entry("a.jpg", b"1"), Entry("b.jpg", b"CHANGED")], b"<x/>")
    with pytest.raises(ArchiveError):
        archive.verify_cbz(dest, entries + [Entry("c.jpg", b"3")], b"<x/>")
    with pytest.raises(ArchiveError):
        archive.verify_cbz(dest, entries[:1], b"<x/>")
    with pytest.raises(ArchiveError, match="ComicInfo"):
        archive.verify_cbz(dest, entries, b"<other/>")
    (tmp_path / "bad.cbz").write_bytes(b"nope")
    with pytest.raises(ArchiveError):
        archive.verify_cbz(str(tmp_path / "bad.cbz"), entries, None)


def test_pdf_pages_are_extracted_losslessly(make_image_pdf):
    path, jpegs = make_image_pdf(pages=3)
    entries, info = archive.read_entries(path, "pdf")
    assert info is None and [e.name for e in entries] == ["page-1.jpg", "page-2.jpg", "page-3.jpg"]
    assert [e.data for e in entries] == jpegs  # byte for byte: nothing was re-encoded


def test_pdf_page_names_sort_correctly_beyond_nine_pages(make_image_pdf):
    path, _ = make_image_pdf(pages=11)
    names = [e.name for e in archive.read_entries(path, "pdf")[0]]
    assert names[0] == "page-01.jpg" and names == sorted(names)


def test_pdf_without_images_falls_back_to_render_in_auto(make_pdf):
    entries, _ = archive.read_entries(make_pdf(pages=2), "pdf", "auto", 72)
    assert [e.name for e in entries] == ["page-1.png", "page-2.png"]
    assert all(e.data.startswith(b"\x89PNG") for e in entries)


def test_pdf_extract_mode_refuses_when_not_possible(make_pdf):
    with pytest.raises(ArchiveError, match="single plain image"):
        archive.read_entries(make_pdf(), "pdf", "extract")


def test_pdf_render_mode_forces_rendering_and_dpi(make_image_pdf):
    path, _ = make_image_pdf(pages=1)
    low, _ = archive.read_entries(path, "pdf", "render", 72)
    high, _ = archive.read_entries(path, "pdf", "render", 144)
    assert low[0].name.endswith(".png") and len(high[0].data) > len(low[0].data)


def test_unreadable_archives_raise_archive_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-garbage")
    with pytest.raises(ArchiveError):
        archive.read_entries(str(bad), "pdf")
    with pytest.raises(ArchiveError):
        archive.read_entries(str(bad), "tar")
