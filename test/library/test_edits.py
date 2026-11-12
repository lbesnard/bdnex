"""Hand edits survive re-imports; unmatched / missing files; relocating paths."""

import os

import pytest

from bdnex.library import AlbumInfo, Library, SeriesInfo


def nains(**kw):
    base = dict(source="b", source_id="1", title="Redwin", series=SeriesInfo("b", "s1", "Nains"), volume=1,
                publisher="Soleil", year=2007, credits={"Writer": ["Jarry, Nicolas"]}, genres=["Fantasy"])
    return AlbumInfo(**{**base, **kw})


@pytest.fixture
def lib():
    return Library(":memory:")


def test_edit_changes_every_kind_of_field(lib):
    album = lib.add_album(nains())
    lib.edit_album(album, {"title": "Redwin de la Forge", "year": None, "publisher": "Delcourt",
                           "credits": {"Writer": ["A"], "Translator": ["B"]}, "genres": ["Humour", "Fantasy"]})
    got = lib.get_album(album)
    assert got["title"] == "Redwin de la Forge" and got["year"] is None and got["publisher"] == "Delcourt"
    assert got["credits"] == {"Translator": ["B"], "Writer": ["A"]} and got["genres"] == ["Fantasy", "Humour"]


def test_an_empty_role_removes_it_and_other_roles_stay(lib):
    album = lib.add_album(nains(credits={"Writer": ["W"], "Penciller": ["P"]}))
    lib.edit_album(album, {"credits": {"Writer": []}})
    assert lib.get_album(album)["credits"] == {"Penciller": ["P"]}


def test_edits_survive_a_new_import_of_the_album(lib):
    album = lib.add_album(nains())
    lib.edit_album(album, {"title": "Mine", "credits": {"Writer": ["Me"]}, "genres": ["Mine"], "series": "Les Nains"})
    lib.add_album(nains(title="From the site", credits={"Writer": ["Site"], "Inker": ["I"]}, genres=["Site"]))
    got = lib.get_album(album)
    assert got["title"] == "Mine" and got["series"] == "Les Nains" and got["genres"] == ["Mine"]
    assert got["credits"] == {"Inker": ["I"], "Writer": ["Me"]}  # roles never edited still follow the site


def test_edits_survive_merge_and_are_not_reported_as_changes(lib):
    album = lib.add_album(nains())
    lib.edit_album(album, {"title": "Mine"})
    fresh = nains(title="From the site", year=2008)
    assert "title" not in " ".join(lib.preview_merge(fresh))
    lib.merge_album(fresh)
    assert lib.get_album(album)["title"] == "Mine" and lib.get_album(album)["year"] == 2008


def test_renaming_a_series_renames_it_for_every_album(lib):
    a = lib.add_album(nains())
    b = lib.add_album(nains(source_id="2", volume=2, title="Two"))
    lib.edit_album(a, {"series": "Les Nains"})
    assert lib.get_album(b)["series"] == "Les Nains"
    lib.add_album(nains(source_id="2", volume=2, title="Two"))  # re-imported with the old name
    assert lib.get_album(b)["series"] == "Les Nains"
    assert sorted(lib.series_albums(a)) == sorted([a, b])


def test_a_oneshot_has_no_series_to_rename(lib):
    album = lib.add_album(nains(series=None, volume=None))
    with pytest.raises(ValueError, match="one-shot"):
        lib.edit_album(album, {"series": "X"})


def test_unknown_field_is_refused(lib):
    album = lib.add_album(nains())
    with pytest.raises(ValueError, match="can not edit: url"):
        lib.edit_album(album, {"url": "x"})


# -------------------------------------------------------------- unmatched files
def test_unmatched_lists_skipped_failed_and_untagged_files_but_not_linked_ones(lib, make_cbz):
    linked, loose, skipped = make_cbz("Linked.cbz"), make_cbz("Loose.cbz"), make_cbz("Skipped é.cbz")
    lib.add_file(linked, lib.add_album(nains()))
    lib.add_file(loose)
    lib.log_import(skipped, "skipped", 55.5)
    lib.log_import("/gone/Failed.cbz", "failed")
    lib.log_import(linked, "skipped")  # an old skip of a file that is linked since
    rows = {os.path.basename(r["path"]): r for r in lib.unmatched()}
    assert set(rows) == {"Loose.cbz", "Skipped é.cbz", "Failed.cbz"}
    assert rows["Skipped é.cbz"]["outcome"] == "skipped" and rows["Skipped é.cbz"]["score"] == 55.5
    assert rows["Loose.cbz"]["outcome"] == "untagged"
    assert [os.path.basename(r["path"]) for r in lib.unmatched(["SKIPPED E"])] == ["Skipped é.cbz"]


def test_only_the_latest_import_counts(lib, make_cbz):
    path = make_cbz("A.cbz")
    lib.log_import(path, "skipped", 40)
    lib.log_import(path, "tagged")
    assert lib.unmatched() == []


def test_missing_files(lib, make_cbz):
    keep, lose = make_cbz("keep.cbz"), make_cbz("lose.cbz")
    lib.add_file(keep)
    lib.add_file(lose)
    os.remove(lose)
    assert [os.path.basename(r["path"]) for r in lib.missing_files()] == ["lose.cbz"]


# -------------------------------------------------------------------- relocate
def test_relocate_rewrites_files_and_log(lib, make_cbz, tmp_path):
    path = make_cbz("A.cbz")
    lib.add_file(path)
    lib.log_import(path, "tagged")
    other = tmp_path / "elsewhere"
    result = lib.relocate(str(tmp_path), str(other))
    assert result["files"] == 1 and result["log"] == 1 and not result["collisions"]
    assert lib.get_file(str(other / "A.cbz")) and lib.get_file(path) is None
    assert lib.last_import(str(other / "A.cbz"))["outcome"] == "tagged"


def test_relocate_only_matches_whole_folders(lib, make_cbz, tmp_path):
    lib.add_file(make_cbz("A.cbz"))
    assert lib.relocate(str(tmp_path)[:-1], "/x")["files"] == 0  # "/tmp/x/test_" is not a parent folder


def test_relocate_pretend_and_collisions_change_nothing(lib, make_cbz, tmp_path):
    path = make_cbz("A.cbz")
    lib.add_file(path)
    assert lib.relocate(str(tmp_path), "/new", apply=False)["files"] == 1
    assert lib.get_file(path)
    lib.conn.execute("INSERT INTO files(path, md5, size, format) VALUES ('/new/A.cbz', 'x', 1, 'cbz')")
    result = lib.relocate(str(tmp_path), "/new")
    assert result["collisions"] == ["/new/A.cbz"] and lib.get_file(path)
