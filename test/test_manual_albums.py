"""Albums written by hand: the form, the album it makes, the file linked."""

import pytest

from bdnex.library import AlbumInfo, Library, manual


def test_a_file_gets_an_album_written_by_hand_and_it_can_be_edited(make_cbz):
    lib = Library(":memory:")
    path = make_cbz("Fell 02 (2005).cbz")
    lib.add_file(path, lib.add_album(AlbumInfo("b", "1", "wrong")))  # a file the review screen shows: it is in the library
    album = manual.create(lib, path, {"series": "Fell", "volume": "2", "publisher": "Image", "year": "2005",
                                      "writer": "Warren Ellis; Ben Templesmith", "genres": "Policier, Horreur"})
    got = lib.get_album(album)
    assert (got["source"], got["series"], got["volume"], got["title"]) == ("manual", "Fell", 2, "Fell - T02")
    assert sorted(got["credits"]["Writer"]) == ["Ben Templesmith", "Warren Ellis"] and sorted(got["genres"]) == ["Horreur", "Policier"]
    assert lib.get_file(path)["album_id"] == album and manual.is_manual(lib, album)
    form = manual.form_of(lib, album)
    assert form["publisher"] == "Image" and sorted(form["writer"].split(", ")) == ["Ben Templesmith", "Warren Ellis"]
    manual.update(lib, album, {**form, "title": "Feral City", "year": "2006"})
    assert lib.get_album(album)["title"] == "Feral City" and lib.get_album(album)["year"] == 2006


def test_several_files_make_one_series_each_with_its_own_volume(make_cbz):
    lib = Library(":memory:")
    paths = [make_cbz(f"Fell 0{n}.cbz") for n in (2, 3)]
    for path in paths:
        lib.add_file(path, lib.add_album(AlbumInfo("b", path, "wrong")))
    ids = manual.create_series(lib, [(paths[0], 2), (paths[1], 3)], {"series": "Fell", "publisher": "Image"})
    albums = [lib.get_album(i) for i in ids]
    assert [a["volume"] for a in albums] == [2, 3] and {a["series"] for a in albums} == {"Fell"}
    assert len(lib.conn.execute("SELECT id FROM series").fetchall()) == 1  # one series, not one per file


def test_the_form_says_what_is_wrong(make_cbz):
    lib = Library(":memory:")
    path = make_cbz("A.cbz")
    with pytest.raises(manual.FormError):
        manual.create(lib, path, {"series": "A", "volume": "abc"})
    with pytest.raises(manual.FormError):
        manual.create_series(lib, [(path, 1)], {"series": " "})


def test_a_first_form_comes_from_the_folder_and_the_name():
    assert manual.guess("/c/Comics/fell/Fell 02 (2005) (c2c).cbz") == {"series": "fell", "volume": "2"}
    assert manual.guess("/c/Comics/Sons Of Anarchy/Sons Of Anarchy - SOA3.pdf")["volume"] == "3"
