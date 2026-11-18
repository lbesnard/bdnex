"""bdnex mark and the attr: query: your own marks on files and albums."""

import io

import pytest
import yaml

from bdnex.library import AlbumInfo, Library
from bdnex.ui import ls_cmd, mark_cmd


@pytest.fixture
def lib_path(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(db) as lib:
        one = lib.add_album(AlbumInfo("bedetheque", "1", "Alpha", language="fr"))
        two = lib.add_album(AlbumInfo("bedetheque", "2", "Bravo", language="fr"))
        lib.add_file(make_cbz("alpha.cbz", pages=3), one)
        lib.add_file(make_cbz("bravo.cbz", pages=4), two)
    return db


def run(*argv):
    out = io.StringIO()
    code = mark_cmd.run(list(argv), out=out)
    return code, out.getvalue()


def names(db, *terms, files=True):
    with Library(db) as lib:
        return [r["path"].rsplit("/", 1)[-1] if files else r["title"] for r in lib.query(list(terms), files=files)]


def test_a_mark_is_set_found_by_name_and_by_value_and_removed(lib_path):
    code, text = run("filename:alpha", "--set", "checked=keep:high", "-y")
    assert code == 0 and "1 file(s): checked=keep:high" in text
    assert names(lib_path, "attr:checked") == ["alpha.cbz"]
    assert names(lib_path, "attr:checked=keep:high") == ["alpha.cbz"] and names(lib_path, "attr:checked=other") == []
    assert names(lib_path, "^attr:checked") == ["bravo.cbz"]  # the files nobody looked at
    run("filename:alpha", "--remove", "checked", "-y")
    assert names(lib_path, "attr:checked") == []


def test_setting_a_mark_again_replaces_its_value(lib_path):
    run("filename:alpha", "--set", "checked=maybe", "-y")
    run("filename:alpha", "--set", "checked=keep", "-y")
    assert names(lib_path, "attr:checked=keep") == ["alpha.cbz"] and names(lib_path, "attr:checked=maybe") == []


def test_albums_can_be_marked_and_a_file_query_sees_its_albums_marks(lib_path):
    assert run("title:bravo", "--albums", "--set", "wanted", "-y")[0] == 0
    assert names(lib_path, "attr:wanted", files=False) == ["Bravo"]
    assert names(lib_path, "attr:wanted") == ["bravo.cbz"]  # the file of that album
    assert names(lib_path, "attr:wanted", files=False)  # and an album query sees marks of its files too


def test_preview_changes_nothing_and_bad_names_are_refused(lib_path, capsys):
    assert run("filename:alpha", "--set", "checked=keep", "-n")[0] == 0 and names(lib_path, "attr:checked") == []
    assert run("filename:alpha", "--set", "Bad Name", "-y")[0] == 2 and "not a mark name" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run("filename:alpha")  # nothing to do


def test_marks_show_in_the_long_listing_and_follow_a_deleted_file(lib_path):
    run("filename:alpha", "--set", "checked=keep", "-y")
    out = io.StringIO()
    ls_cmd.run(["--files", "-l", "filename:alpha"], out=out)
    assert "marks: checked=keep" in out.getvalue()
    with Library(lib_path) as lib:
        lib.conn.execute("DELETE FROM files WHERE path LIKE '%alpha.cbz'")
        lib.conn.commit()
        assert lib.conn.execute("SELECT COUNT(*) FROM file_attrs").fetchone()[0] == 0  # gone with the file
