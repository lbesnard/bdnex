import os
import zipfile

import pytest

from bdnex.library import AlbumInfo, Library
from bdnex.library.hashing import file_md5
from bdnex.library.scan import detect_format, inspect_file, iter_comic_files


@pytest.fixture
def lib():
    with Library(":memory:") as library:
        yield library


def test_detect_format(make_cbz, make_pdf, tmp_path):
    assert detect_format(make_cbz()) == "cbz"
    assert detect_format(make_pdf()) == "pdf"
    junk = tmp_path / "x.cbz"
    junk.write_bytes(b"not an archive")
    assert detect_format(junk) is None


def test_inspect_cbz(make_cbz):
    info = inspect_file(make_cbz(pages=4, comicinfo=True))
    assert (info.format, info.page_count) == ("cbz", 4)
    assert info.has_metadata and not info.corrupted


def test_inspect_pdf(make_pdf):
    info = inspect_file(make_pdf(pages=3))
    assert (info.format, info.page_count, info.corrupted) == ("pdf", 3, False)


def test_inspect_real_samples():
    here = os.path.dirname(os.path.dirname(__file__))
    cbz = inspect_file(os.path.join(here, "bd.cbz"))
    assert cbz.format == "cbz" and not cbz.corrupted and cbz.page_count > 0


def test_inspect_corrupted_cbz_flagged_not_raised(tmp_path):
    bad = tmp_path / "bad.cbz"
    bad.write_bytes(b"PK\x03\x04 truncated garbage")
    info = inspect_file(bad)
    assert info.corrupted


def test_inspect_unknown_content_flagged(tmp_path):
    f = tmp_path / "weird.cbz"
    f.write_bytes(b"hello")
    assert inspect_file(f).corrupted


def test_md5_matches_hashlib(tmp_path):
    import hashlib
    f = tmp_path / "f.bin"
    f.write_bytes(b"abc" * 1000)
    assert file_md5(f) == hashlib.md5(b"abc" * 1000).hexdigest()


def test_iter_comic_files_recursive_and_filtered(make_cbz, tmp_path):
    a = make_cbz("a.cbz")
    b = make_cbz("b.CBZ", directory=tmp_path / "sub")
    (tmp_path / "notes.txt").write_text("x")
    assert list(iter_comic_files(tmp_path)) == sorted([a, b])


def test_add_file_records_checksum(lib, make_cbz):
    path = make_cbz()
    lib.add_file(path)
    row = lib.get_file(path)
    assert row["md5"] == file_md5(path)
    assert row["format"] == "cbz" and row["page_count"] == 3
    assert row["corrupted"] == 0 and row["album_id"] is None


def test_refresh_after_write_updates_md5_and_metadata_flag(lib, make_cbz):
    path = make_cbz()
    lib.add_file(path)
    before = lib.get_file(path)
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("ComicInfo.xml", "<ComicInfo/>")
    lib.refresh_file(path)
    after = lib.get_file(path)
    assert after["md5"] != before["md5"] and after["md5"] == file_md5(path)
    assert after["has_metadata"] == 1
    assert after["id"] == before["id"]


def test_readd_keeps_album_link(lib, make_cbz):
    album_id = lib.add_album(AlbumInfo("s", "1", "T"))
    path = make_cbz()
    lib.add_file(path, album_id)
    lib.add_file(path)  # rescan without album
    assert lib.get_file(path)["album_id"] == album_id


def test_move_file_updates_path_and_checksum(lib, make_cbz, tmp_path):
    path = make_cbz()
    lib.add_file(path)
    file_id = lib.get_file(path)["id"]
    new = str(tmp_path / "lib" / "moved.cbz")
    os.makedirs(os.path.dirname(new))
    os.rename(path, new)
    lib.move_file(path, new)
    assert lib.get_file(path) is None
    assert lib.get_file(new)["id"] == file_id


def test_move_unknown_file_raises(lib, tmp_path):
    with pytest.raises(KeyError):
        lib.move_file(tmp_path / "a", tmp_path / "b")


def test_scan_counts_and_rescan(lib, make_cbz, tmp_path):
    make_cbz("a.cbz")
    make_cbz("b.cbz", directory=tmp_path / "d")
    (tmp_path / "bad.cbz").write_bytes(b"nope")
    assert lib.scan(tmp_path) == {"added": 3, "updated": 0, "corrupted": 1}
    assert lib.scan(tmp_path) == {"added": 0, "updated": 3, "corrupted": 1}
    assert lib.stats()["files"] == 3


def test_duplicate_files_by_checksum(lib, make_cbz, tmp_path):
    a = make_cbz("a.cbz")
    b = str(tmp_path / "copy.cbz")
    with open(a, "rb") as src, open(b, "wb") as dst:
        dst.write(src.read())
    make_cbz("other.cbz", pages=5)
    lib.scan(tmp_path)
    groups = lib.duplicate_files()
    assert len(groups) == 1
    assert {f["path"] for f in groups[0]} == {a, b}


def test_set_file_album_and_remove(lib, make_cbz):
    path = make_cbz()
    lib.add_file(path)
    album_id = lib.add_album(AlbumInfo("s", "1", "T"))
    lib.set_file_album(path, album_id)
    assert lib.get_file(path)["album_id"] == album_id
    assert lib.stats()["untagged"] == 0
    lib.remove_file(path)
    assert lib.get_file(path) is None


def test_inspect_distinguishes_damaged_from_unreadable(make_damaged_cbz, tmp_path):
    damaged, _ = make_damaged_cbz()
    info = inspect_file(damaged)
    assert info.corrupted and info.readable and info.problem == "page p01.jpg failed its checksum"
    junk = tmp_path / "x.cbz"
    junk.write_bytes(b"hello")
    info = inspect_file(junk)
    assert info.corrupted and not info.readable and info.problem


def test_relink_finds_a_file_moved_outside_bdnex_by_its_content(lib, make_cbz, tmp_path):
    original = make_cbz("a.cbz", pages=3)
    lib.add_file(original)
    other = make_cbz("b.cbz", pages=5)
    lib.add_file(other)
    moved = tmp_path / "elsewhere" / "renamed.cbz"
    moved.parent.mkdir()
    os.rename(original, moved)
    os.remove(other)
    result = lib.relink([str(tmp_path)], apply=False)
    assert result["found"] == [(original, str(moved))] and result["lost"] == [other]
    assert lib.get_file(str(moved)) is None  # a preview changes nothing
    lib.relink([str(tmp_path)])
    assert lib.get_file(str(moved)) is not None and lib.get_file(original) is None
    assert [r["path"] for r in lib.missing_files()] == [other]


def test_analysing_many_files_in_processes_gives_the_same_answers_in_order(make_cbz, tmp_path):
    from bdnex.library.database import analyse_many
    paths = [make_cbz(f"b{n}.cbz", pages=2 + n) for n in range(6)]
    paths.append(str(tmp_path / "missing.cbz"))
    serial = list(analyse_many(paths, workers=1))
    parallel = list(analyse_many(paths, workers=3))
    assert [p for p, _ in parallel] == paths
    assert [(a[0].page_count, a[1]) for _, a in parallel[:-1]] == [(a[0].page_count, a[1]) for _, a in serial[:-1]]
    assert isinstance(parallel[-1][1], Exception)  # one bad path is an answer, not a crash


def test_add_analysed_registers_what_add_file_would(lib, make_cbz):
    from bdnex.library.database import analyse_file
    path = make_cbz("a.cbz", pages=4)
    info, md5 = analyse_file(path)
    lib.add_analysed(info, md5)
    assert lib.get_file(path)["md5"] == md5 and lib.get_file(path)["page_count"] == 4


def test_relink_reads_the_candidates_in_several_processes_with_the_same_answer(lib, make_cbz, tmp_path):
    originals = [make_cbz(f"r{n}.cbz", pages=2 + n) for n in range(5)]
    for path in originals:
        lib.add_file(path)
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    for path in originals:
        os.rename(path, moved / ("renamed-" + os.path.basename(path)))
    serial = lib.relink([str(tmp_path)], apply=False, workers=1)
    parallel = lib.relink([str(tmp_path)], apply=False, workers=3)
    assert len(serial["found"]) == 5 and serial == parallel
