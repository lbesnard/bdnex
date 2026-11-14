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
    low, _ = archive.read_entries(path, "pdf", "render", 72, "png")
    high, _ = archive.read_entries(path, "pdf", "render", 144, "png")
    assert low[0].name.endswith(".png") and len(high[0].data) > len(low[0].data)


def test_unreadable_archives_raise_archive_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-garbage")
    with pytest.raises(ArchiveError):
        archive.read_entries(str(bad), "pdf")
    with pytest.raises(ArchiveError):
        archive.read_entries(str(bad), "tar")


def test_lenient_read_recovers_a_page_with_a_wrong_checksum(make_damaged_cbz):
    path, originals = make_damaged_cbz()
    with pytest.raises(ArchiveError):
        archive.read_entries(path, "cbz")  # the strict reader refuses it
    result = archive.read_entries_lenient(path, "cbz")
    assert result.ok and result.repaired == ["p01.jpg"] and result.lost == []
    assert {e.name for e in result.entries} == set(originals)
    assert {e.name: e.data for e in result.entries if e.name != "p01.jpg"} == \
        {k: v for k, v in originals.items() if k != "p01.jpg"}


def test_lenient_read_reports_pages_it_cannot_recover(make_damaged_cbz):
    path, _ = make_damaged_cbz(damage="junk")
    result = archive.read_entries_lenient(path, "cbz")
    assert not result.ok and result.repaired == [] and len(result.lost) == 1 and "p01.jpg" in result.lost[0]


def test_lenient_read_of_a_healthy_archive_changes_nothing():
    result = archive.read_entries_lenient(BD_CBZ, "cbz")
    assert result.ok and result.repaired == [] and sorted(e.name for e in result.entries) == \
        ["bd/a.jpg", "bd/b.jpg", "bd/c.gif"]


def test_lenient_read_of_formats_it_cannot_repair(make_pdf):
    assert not archive.read_entries_lenient(make_pdf(), "pdf").ok


def test_zip_sources_keep_each_pages_compression(tmp_path):
    src = str(tmp_path / "s.cbz")
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("a.jpg", b"A" * 1000, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("b.jpg", b"B" * 1000, compress_type=zipfile.ZIP_STORED)
    entries, _ = archive.read_entries(src, "cbz")
    out = str(tmp_path / "o.cbz")
    archive.write_cbz(out, entries, None)
    with zipfile.ZipFile(out) as zf:
        assert zf.getinfo("a.jpg").compress_type == zipfile.ZIP_DEFLATED  # not re-decided from ".jpg"
        assert zf.getinfo("b.jpg").compress_type == zipfile.ZIP_STORED
    assert zipfile.ZipFile(out).getinfo("a.jpg").compress_size < 100
    archive.verify_cbz(out, entries, None)


def test_entries_from_other_formats_still_use_the_extension_rule(tmp_path):
    out = str(tmp_path / "o.cbz")
    archive.write_cbz(out, [Entry("p.jpg", b"x" * 100), Entry("n.txt", b"x" * 100)], None)
    with zipfile.ZipFile(out) as zf:
        assert zf.getinfo("p.jpg").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("n.txt").compress_type == zipfile.ZIP_DEFLATED


# ----------------------------------------------------------- page by page
def mixed_pdf(tmp_path, build):
    import pymupdf
    from conftest import make_jpeg_bytes

    doc = pymupdf.open()
    build(doc, make_jpeg_bytes)
    path = str(tmp_path / "mixed.pdf")
    doc.save(path)
    doc.close()
    return path


def test_one_odd_page_no_longer_forces_every_page_to_be_drawn(tmp_path):
    def build(doc, jpeg):
        for color in ((200, 20, 20), (20, 200, 20)):
            page = doc.new_page(width=200, height=300)
            page.insert_image(page.rect, stream=jpeg(color=color))
        doc.new_page(width=200, height=300).insert_text((20, 50), "a page of text")
    entries, _ = archive.read_entries(mixed_pdf(tmp_path, build), "pdf", "auto", 72)
    assert [e.rendered for e in entries] == [False, False, True]
    assert entries[0].name.endswith(".jpg") and entries[0].data[:2] == b"\xff\xd8"  # a page taken out untouched
    assert entries[2].name.endswith(".png")  # text: PNG


def test_extract_mode_names_the_pages_it_cannot_take(tmp_path):
    def build(doc, jpeg):
        doc.new_page(width=200, height=300).insert_image(fitz_rect(), stream=jpeg())
        doc.new_page(width=200, height=300).insert_text((20, 50), "text")
        doc.new_page(width=200, height=300).insert_text((20, 50), "text")

    def fitz_rect():
        import pymupdf
        return pymupdf.Rect(0, 0, 200, 300)
    with pytest.raises(ArchiveError, match=r"page\(s\) 2, 3"):
        archive.read_entries(mixed_pdf(tmp_path, build), "pdf", "extract")


def test_a_small_logo_beside_the_page_image_does_not_stop_the_extraction(tmp_path):
    import pymupdf

    def build(doc, jpeg):
        page = doc.new_page(width=200, height=300)
        page.insert_image(page.rect, stream=jpeg(color=(10, 10, 200)))
        page.insert_image(pymupdf.Rect(5, 5, 15, 15), stream=jpeg(color=(250, 250, 0), size=(11, 13)))
    entries, _ = archive.read_entries(mixed_pdf(tmp_path, build), "pdf", "auto", 72)
    assert [e.rendered for e in entries] == [False]


def test_two_big_pictures_on_a_page_are_drawn(tmp_path):
    import pymupdf

    def build(doc, jpeg):
        page = doc.new_page(width=200, height=300)
        page.insert_image(pymupdf.Rect(0, 0, 200, 150), stream=jpeg(color=(10, 10, 200)))
        page.insert_image(pymupdf.Rect(0, 150, 200, 300), stream=jpeg(color=(200, 10, 10)))
    entries, _ = archive.read_entries(mixed_pdf(tmp_path, build), "pdf", "auto", 72)
    assert [e.rendered for e in entries] == [True]


def test_drawn_pages_of_jpeg_pictures_are_drawn_as_jpeg_in_auto(tmp_path):
    import pymupdf

    def build(doc, jpeg):
        page = doc.new_page(width=200, height=300)
        page.insert_image(pymupdf.Rect(0, 0, 200, 150), stream=jpeg(color=(10, 10, 200)))
        page.insert_image(pymupdf.Rect(0, 150, 200, 300), stream=jpeg(color=(200, 10, 10)))
    path = mixed_pdf(tmp_path, build)
    auto, _ = archive.read_entries(path, "pdf", "auto", 150)
    png, _ = archive.read_entries(path, "pdf", "auto", 150, "png")
    jpg, _ = archive.read_entries(path, "pdf", "auto", 150, "jpeg")
    assert auto[0].name.endswith(".jpg") and png[0].name.endswith(".png") and jpg[0].name.endswith(".jpg")
    assert auto[0].data[:2] == b"\xff\xd8" and png[0].data.startswith(b"\x89PNG")




def test_a_picture_in_a_format_viewers_do_not_open_is_decoded_to_png_with_the_same_pixels(tmp_path, monkeypatch):
    import pymupdf

    source = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 60), False)
    source.set_rect(source.irect, (10, 200, 30))
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    page.insert_image(page.rect, pixmap=source)
    path = str(tmp_path / "odd.pdf")
    doc.save(path)
    doc.close()
    # pretend PNG is a format that cannot be copied out as it is (as JPEG 2000 or JBIG2 would be)
    monkeypatch.setattr(archive, "PDF_EXTRACTABLE", {"jpg", "jpeg"})
    (entry,), _ = archive.read_entries(path, "pdf", "auto", 72)
    assert entry.decoded and not entry.rendered and entry.name.endswith(".png")
    back = pymupdf.Pixmap(entry.data)
    assert (back.width, back.height) == (40, 60) and back.samples == source.samples  # not resampled, not altered


def test_a_page_with_a_photo_layer_and_another_layer_is_drawn_as_jpeg(tmp_path):
    """A JPEG background under a PNG-like layer (a scanner's layers) is photographic: PNG of it was ~10x bigger."""
    import pymupdf

    def build(doc, jpeg):
        page = doc.new_page(width=200, height=300)
        page.insert_image(page.rect, stream=jpeg(color=(10, 10, 200)))
        layer = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
        layer.clear_with(255)
        page.insert_image(pymupdf.Rect(0, 0, 200, 300), stream=layer.tobytes("png"))
    entries, _ = archive.read_entries(mixed_pdf(tmp_path, build), "pdf", "auto", 72)
    assert entries[0].rendered and entries[0].name.endswith(".jpg")


def test_a_page_is_not_drawn_sharper_than_its_pictures(tmp_path):
    import pymupdf

    def build(doc, jpeg):
        page = doc.new_page(width=200, height=300)  # 400 px over 200 pt: 144 dpi
        page.insert_image(pymupdf.Rect(0, 0, 200, 150), stream=jpeg(color=(10, 10, 200), size=(400, 300)))
        page.insert_image(pymupdf.Rect(0, 150, 200, 300), stream=jpeg(color=(200, 10, 10), size=(400, 300)))
    entries, _ = archive.read_entries(mixed_pdf(tmp_path, build), "pdf", "auto", 300, "png")
    assert int.from_bytes(entries[0].data[16:20], "big") == 400  # not 833, which 300 dpi would give


def test_jpeg_quality_of_a_drawn_page_is_a_setting(tmp_path):
    import pymupdf

    def build(doc, jpeg):
        page = doc.new_page(width=200, height=300)
        page.insert_image(pymupdf.Rect(0, 0, 200, 150), stream=jpeg(color=(10, 10, 200)))
        page.insert_image(pymupdf.Rect(0, 150, 200, 300), stream=jpeg(color=(200, 10, 10)))
        page.insert_text((20, 50), "Noisy text 12345 abcdef", fontsize=20)
    path = mixed_pdf(tmp_path, build)
    low = archive.read_entries(path, "pdf", "auto", 150, "jpeg", 20)[0][0].data
    high = archive.read_entries(path, "pdf", "auto", 150, "jpeg", 98)[0][0].data
    assert len(high) > len(low)
