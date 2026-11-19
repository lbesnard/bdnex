"""bdnex update: read the files again after something else changed them."""

import io
import os
import zipfile

import pytest
import yaml

from bdnex.library import AlbumInfo, Library
from bdnex.ui import update_cmd


@pytest.fixture
def lib_path(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(db) as lib:
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Alpha", language="fr"))
        lib.add_file(make_cbz("alpha.cbz", pages=3), album)
        lib.add_file(make_cbz("bravo.cbz", pages=4), album)
    return db


def run(*argv):
    out = io.StringIO()
    return update_cmd.run(list(argv), out=out), out.getvalue()


def test_nothing_changed_reads_nothing(lib_path):
    code, text = run()
    assert code == 0 and "0 read" in text


def test_a_changed_file_is_read_again_and_keeps_its_album(lib_path, tmp_path):
    path = str(tmp_path / "alpha.cbz")
    with zipfile.ZipFile(path, "a") as archive:  # another tool added a page
        archive.writestr("page-99.jpg", b"x" * 50)
    code, text = run("-n")
    assert code == 0 and "would read" in text and "alpha.cbz" in text and "bravo.cbz" not in text
    with Library(lib_path) as lib:
        before = lib.get_file(path)
    code, text = run()
    assert code == 0 and "content changed" in text and "pages 3 -> 4" in text
    with Library(lib_path) as lib:
        after = lib.get_file(path)
    assert after["md5"] != before["md5"] and after["page_count"] == 4 and after["album_id"] == before["album_id"]


def test_all_reads_every_file_and_a_missing_file_is_listed(lib_path, tmp_path):
    os.remove(tmp_path / "bravo.cbz")
    code, text = run("--all")
    assert code == 0 and "1 missing" in text and "bravo.cbz" in text and "bdnex relocate --find" in text
    assert "1 read" in text  # alpha only
