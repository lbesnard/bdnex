"""bdnex stats and bdnex fields."""

import io
import json

import pytest
import yaml

from bdnex.library import AlbumInfo, Library
from bdnex.library.query import ALL_FIELDS
from bdnex.ui import ls_cmd, stats_cmd


@pytest.fixture
def lib_path(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(db) as lib:
        full = lib.add_album(AlbumInfo("bedetheque", "1", "Lu", language="fr"))
        thin = lib.add_album(AlbumInfo("bedetheque", "2", "Pas lu", language="fr", extra={"partial": True}))
        lib.add_file(make_cbz("a.cbz", pages=3), full)
        lib.add_file(make_cbz("b.cbz", pages=4), thin)
        lib.add_file(make_cbz("c.cbz", pages=2), None)
    return db


def test_every_query_field_has_a_line_of_help():
    assert [name for name in ALL_FIELDS if name not in stats_cmd.FIELD_HELP] == []
    assert [name for name in stats_cmd.FIELD_HELP if name not in ALL_FIELDS] == []  # and none is left over


def test_fields_lists_each_field_with_its_kind():
    out = io.StringIO()
    assert ls_cmd.run(["--fields"], out=out) == 0  # no library needed to read the list
    text = out.getvalue()
    assert "year" in text and "number" in text and "complete" in text and "yes/no" in text


def test_stats_counts_albums_read_or_not_and_files(lib_path):
    out = io.StringIO()
    assert stats_cmd.run(["--json"], out=out) == 0
    data = json.loads(out.getvalue())
    assert data["albums"] == {"total": 2, "complete": 1, "partial": 1, "series": 0, "oneshots": 2}
    assert data["files"]["total"] == 3 and data["files"]["linked"] == 2 and data["files"]["unlinked"] == 1
    assert data["files"]["by_format"]["cbz"]["files"] == 3


def test_stats_text_and_a_query_narrow_it(lib_path):
    out = io.StringIO()
    assert stats_cmd.run(["title:lu", "--json"], out=out) == 0
    assert json.loads(out.getvalue())["albums"]["total"] == 2  # "Lu" is inside "Pas lu"
    out = io.StringIO()
    stats_cmd.run([], out=out)
    assert "Albums: 2" in out.getvalue() and "only matched (partial): 1" in out.getvalue()
