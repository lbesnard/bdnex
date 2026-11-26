"""Files of one album: compare them, trash a true duplicate, swap main and spare, keep one as a version of its own."""

import shutil

import pytest

from bdnex.config import build_settings
from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.library import twins


@pytest.fixture
def env(tmp_path, make_cbz):
    root = tmp_path / "comics"
    root.mkdir()
    settings = build_settings({"config_version": 2, "collection_path": str(root), "library": str(tmp_path / "l.sqlite")})[0]
    library = Library.for_settings(settings)
    album = library.add_album(AlbumInfo("b", "1", "Kishinev-des-fous", series=SeriesInfo("b", "s", "Klezmer"), volume=5, year=2012))
    main, spare = str(root / "Klezmer" / "T05.cbz"), str(root / "Klezmer" / "T05 bis.cbz")
    (root / "Klezmer").mkdir()
    shutil.copy(make_cbz("a.cbz", pages=4), main)
    shutil.copy(make_cbz("b.cbz", pages=7), spare)
    library.add_file(main, album)
    library.add_file(spare, album)
    library.set_file_fields(spare, spare=1)
    return settings, library, album, main, spare


def test_the_twins_are_listed_main_first_with_what_tells_them_apart(env):
    settings, library, album, main, spare = env
    found = twins.twins_of(library, spare)
    assert [(t.path, t.main) for t in found] == [(main, True), (spare, False)]
    text = "\n".join(twins.describe(found, ["cbz"], str(settings.collection_path)))
    assert "MAIN  Klezmer/T05.cbz" in text and "spare Klezmer/T05 bis.cbz" in text and "4 pages" in text and "7 pages" in text
    assert "different page count" in text and "the other file" in text  # 7 pages beat 4
    assert twins.twins_of(library, "/nowhere.cbz") == []


def test_a_file_alone_has_no_twins(env):
    settings, library, album, main, spare = env
    twins.trash_file(library, settings, spare)
    assert twins.twins_of(library, main) == []


def test_trashing_the_main_file_promotes_the_spare_and_keeps_it_restorable(env):
    settings, library, album, main, spare = env
    twins.trash_file(library, settings, main)
    assert library.get_file(spare)["spare"] == 0 and library.get_file(main) is None
    (record,) = library.trash_entries()
    assert record["album_id"] == album and record["reason"].startswith("a true duplicate")


def test_making_the_spare_the_main_file_swaps_them(env):
    settings, library, album, main, spare = env
    assert twins.make_main(library, spare) == 2
    assert (library.get_file(spare)["spare"], library.get_file(main)["spare"]) == (0, 1)
    assert twins.make_main(library, spare) == 0


def test_a_different_version_gets_an_album_of_its_own_linked_to_the_first(env):
    settings, library, album, main, spare = env
    new = twins.keep_as_version(library, spare, "version", "N&B")
    got = library.get_album(new)
    assert got["source"] == "manual" and got["title"] == "Kishinev-des-fous (N&B)" and got["volume"] == 5 and got["series"] == "Klezmer"
    assert got["parent_id"] == album and got["relation"] == "version"
    assert library.get_file(spare)["album_id"] == new and library.get_file(spare)["spare"] == 0
    assert twins.twins_of(library, main) == []  # no longer two files of one album
    assert library.related_albums(album)["children"][0]["id"] == new
    lines = [line["text"] for line in twins.album_link_lines(library, new)]
    assert lines == [f"version of album {album}: Kishinev-des-fous"]


def test_the_first_album_can_be_the_child(env):
    settings, library, album, main, spare = env
    new = twins.keep_as_version(library, spare, "child", "", other_is_parent=False)
    assert library.get_album(album)["parent_id"] == new and library.get_album(album)["relation"] == "child"
    assert library.get_album(new)["title"] == "Kishinev-des-fous (version)"
