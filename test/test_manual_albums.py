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
    assert form["publisher"] == "Image" and sorted(form["writer"].split("; ")) == ["Ben Templesmith", "Warren Ellis"]
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


def test_a_file_the_import_skipped_is_registered_with_its_hand_written_album(make_cbz):
    lib = Library(":memory:")
    path = make_cbz("Skipped.cbz")
    album = manual.create(lib, path, {"series": "S", "volume": "1"})
    assert lib.get_file(path)["album_id"] == album


def test_two_series_names_that_fold_alike_stay_two_series_and_one_name_stays_one(make_cbz):
    from bdnex.library import manual

    assert manual.series_key("C++") != manual.series_key("C#") and manual.series_key("!!") != manual.series_key("??")
    assert manual.series_key("Les Nains") == manual.series_key("Les Nains ")
    with Library(":memory:") as lib:
        a = manual.create(lib, make_cbz("a.cbz"), {"series": "C++"}, 1)
        b = manual.create(lib, make_cbz("b.cbz"), {"series": "C#"}, 1)
        c = manual.create(lib, make_cbz("c.cbz"), {"series": "C++"}, 2)
        assert lib.get_album(a)["series"] == "C++" and lib.get_album(b)["series"] == "C#"  # the second did not rename the first
        assert lib.get_album(c)["series"] == "C++" and lib.series_albums(a) == [a, c]


def test_a_name_with_a_comma_is_not_split_when_the_form_is_saved_again(make_cbz):
    from bdnex.library import manual

    with Library(":memory:") as lib:
        album_id = manual.create(lib, make_cbz("a.cbz"), {"series": "S", "writer": "Dupuy, Philippe; Berberian"}, 1)
        form = manual.form_of(lib, album_id)
        manual.update(lib, album_id, form)
        assert sorted(lib.get_album(album_id)["credits"]["Writer"]) == ["Berberian", "Dupuy, Philippe"]
