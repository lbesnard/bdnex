"""File paths under the library folder are stored relative to it."""

import os
import shutil

import pytest

from bdnex.config import build_settings
from bdnex.library import AlbumInfo, Library, LibraryRootError, SeriesInfo, SCHEMA_VERSION


def stored(db):
    import sqlite3
    conn = sqlite3.connect(db)
    return [r[0] for r in conn.execute("SELECT path FROM files ORDER BY path")]


@pytest.fixture
def tree(tmp_path, make_cbz):
    root = tmp_path / "comics"
    (root / "Series").mkdir(parents=True)
    inside = root / "Series" / "A.cbz"
    shutil.copy(make_cbz("A.cbz"), inside)
    outside = tmp_path / "elsewhere" / "B.cbz"
    outside.parent.mkdir()
    shutil.copy(make_cbz("B.cbz"), outside)
    return root, str(inside), str(outside), tmp_path / "lib.sqlite"


def test_inside_files_are_stored_relative_and_outside_ones_absolute(tree):
    root, inside, outside, db = tree
    with Library(db, root=root) as lib:
        lib.add_file(inside)
        lib.add_file(outside)
    assert stored(db) == [outside, os.path.join("Series", "A.cbz")]


def test_the_api_stays_absolute(tree):
    root, inside, outside, db = tree
    with Library(db, root=root) as lib:
        album = lib.add_album(AlbumInfo("b", "1", "T", series=SeriesInfo("b", "s", "S"), volume=1))
        lib.add_file(inside, album)
        lib.add_file(outside, album)
        assert lib.get_file(inside)["path"] == inside
        assert [f["path"] for f in lib.files_for_album(album)] == sorted([inside, outside])
        assert [r["path"] for r in lib.query([], files=True)] == sorted([inside, outside])
        assert lib.files_matching_album("b", "1", {}, inside)[0]["path"] == outside
        lib.log_import(inside, "tagged")
        assert lib.last_import(inside)["path"] == inside


def test_moving_the_collection_is_changing_the_root(tree, tmp_path):
    root, inside, _, db = tree
    with Library(db, root=root) as lib:
        lib.add_file(inside)
        lib.log_import(inside, "tagged")
    new_root = tmp_path / "nas"
    shutil.move(str(root), str(new_root))
    with Library(db, root=new_root) as lib:
        moved = str(new_root / "Series" / "A.cbz")
        assert lib.get_file(moved)["path"] == moved and lib.missing_files() == []
        assert lib.last_import(moved)["outcome"] == "tagged"
    assert stored(db) == [os.path.join("Series", "A.cbz")]  # nothing was rewritten


def test_a_wrong_root_makes_files_missing_not_lost(tree, tmp_path):
    root, inside, _, db = tree
    with Library(db, root=root) as lib:
        lib.add_file(inside)
    with Library(db, root=tmp_path / "wrong") as lib:
        assert len(lib.missing_files()) == 1
    with Library(db, root=root) as lib:
        assert lib.missing_files() == []


def test_existing_absolute_paths_under_the_root_are_converted_on_open(tree):
    root, inside, outside, db = tree
    with Library(db) as lib:  # no root: absolute, like a library from before
        lib.add_file(inside)
        lib.add_file(outside)
        lib.log_import(inside, "tagged")
    assert inside in stored(db)
    with Library(db, root=root) as lib:
        assert lib.get_file(inside)["path"] == inside and lib.last_import(inside)["outcome"] == "tagged"
    assert stored(db) == [outside, os.path.join("Series", "A.cbz")]


def test_a_folder_with_a_similar_name_is_not_under_the_root(tree, tmp_path, make_cbz):
    root, _, _, db = tree
    sibling = tmp_path / "comics2" / "C.cbz"
    sibling.parent.mkdir()
    shutil.copy(make_cbz("C.cbz"), sibling)
    with Library(db, root=root) as lib:
        lib.add_file(str(sibling))
    assert stored(db) == [str(sibling)]


def test_relative_paths_without_a_root_are_refused_with_an_explanation(tree):
    root, inside, _, db = tree
    with Library(db, root=root) as lib:
        lib.add_file(inside)
    with pytest.raises(LibraryRootError, match="collection_path"):
        Library(db)
    with Library(db, root=root) as lib:  # and the file is fine
        assert lib.get_file(inside)


def test_moving_a_file_inside_the_root_updates_the_stored_path(tree):
    root, inside, _, db = tree
    new = root / "Series" / "Renamed.cbz"
    with Library(db, root=root) as lib:
        lib.add_file(inside)
        os.rename(inside, new)
        lib.move_file(inside, str(new))
        assert lib.get_file(str(new)) and lib.get_file(inside) is None
    assert stored(db) == [os.path.join("Series", "Renamed.cbz")]


def test_relocate_handles_outside_files_and_stores_relative_when_it_lands_in_the_root(tree, tmp_path):
    root, _, outside, db = tree
    with Library(db, root=root) as lib:
        lib.add_file(outside)
        target = root / "Series" / "B.cbz"
        result = lib.relocate(os.path.dirname(outside), str(root / "Series"))
        assert result["files"] == 1 and result["moved"] == [(outside, str(target))]
        assert lib.get_file(str(target)) is not None
    assert stored(db) == [os.path.join("Series", "B.cbz")]


def test_for_settings_uses_the_configured_directory_and_ignores_the_placeholder(tree, tmp_path):
    root, inside, _, _ = tree
    settings = build_settings({"config_version": 2, "library": str(tmp_path / "s.sqlite"), "collection_path": str(root)})[0]
    with Library.for_settings(settings) as lib:
        assert lib.root == str(root)
    placeholder = build_settings({"config_version": 2, "library": str(tmp_path / "p.sqlite")})[0]
    with Library.for_settings(placeholder) as lib:
        assert lib.root is None


def test_schema_version_two_refuses_to_be_read_by_version_one(tree):
    _, _, _, db = tree
    Library(db).close()
    import sqlite3
    assert sqlite3.connect(db).execute("SELECT value FROM meta WHERE key = 'paths'").fetchone()[0] == "relative"
    assert SCHEMA_VERSION >= 2
