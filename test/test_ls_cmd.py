import io
import json

import pytest
import yaml

from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.ui import ls_cmd


@pytest.fixture
def setup(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(db) as lib:
        a = lib.add_album(AlbumInfo("bedetheque", "1", "Redwin de la Forge",
                                    series=SeriesInfo("bedetheque", "47467", "Nains"), volume=1,
                                    year=2015, credits={"Writer": ["Jarry, Nicolas"]}, genres=["Fantasy"]))
        b = lib.add_album(AlbumInfo("bedetheque", "2", "La bibliomule de Cordoue", year=2021,
                                    credits={"Writer": ["Lupano, Wilfrid"]}, extra={"partial": True}))
        lib.add_file(make_cbz("nains1.cbz", pages=3), a)
        lib.add_file(make_cbz("bib.cbz", pages=4), b)
        lib.add_file(make_cbz("loose.cbz", pages=5))
    return tmp_path


def run(*argv):
    out = io.StringIO()
    code = ls_cmd.run(list(argv), out=out)
    return code, out.getvalue()


def test_default_listing(setup):
    code, text = run()
    assert code == 0
    lines = text.strip().splitlines()
    assert lines[0] == "La bibliomule de Cordoue [not fetched]  (Lupano, Wilfrid) [2021]  1 file"
    assert lines[1] == "Nains - T01 - Redwin de la Forge  (Jarry, Nicolas) [2015]  1 file"


def test_query_and_count(setup):
    assert run("author:lupano") [1].startswith("La bibliomule")
    assert run("-c", "series:nains") == (0, "1\n")
    assert run("-c", "^series:nains") == (0, "1\n")


def test_custom_format(setup):
    assert run("--format", "$series|$vol|$title", "series:nains") == (0, "Nains|01|Redwin de la Forge\n")
    assert run("--format", "$unknown-$year", "year:2021") == (0, "$unknown-2021\n")  # safe_substitute


def test_files_listing_shows_untagged(setup):
    code, text = run("--files", "tagged:false")
    assert code == 0 and "loose.cbz" in text and "(not imported)" in text


def test_json_output(setup):
    code, text = run("--json", "year:2015")
    data = json.loads(text)
    assert code == 0 and data[0]["title"] == "Redwin de la Forge" and data[0]["genres"] == "Fantasy"


def test_bad_query_exit_code_2(setup, capsys):
    assert run("year:abc")[0] == 2
    assert "bad query" in capsys.readouterr().err


def test_no_library_yet(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "none.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert run()[0] == 1
    assert "bdnex import" in capsys.readouterr().err
    assert not (tmp_path / "none.sqlite").exists()  # ls must not create an empty library


def test_invalid_config(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "bad.yaml"
    conf.write_text("config_version: 2\noops: 1\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert run()[0] == 1 and "oops" in capsys.readouterr().err


def test_main_dispatches_ls(monkeypatch, tmp_path):
    from bdnex import ui
    conf = tmp_path / "c.yaml"
    conf.write_text("config_version: 2\ncollection_path: ~/c\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    seen = {}
    monkeypatch.setattr("bdnex.ui.ls_cmd.run", lambda argv: seen.setdefault("argv", argv) and 0)
    with pytest.raises(SystemExit) as exit_:
        ui.main(["ls", "series:nains"])
    assert seen["argv"] == ["series:nains"] and exit_.value.code == 0


def test_long_view_shows_everything_known(setup):
    code, text = run("--long", "series:nains")
    assert code == 0
    assert text.splitlines()[0] == "Nains - T01 - Redwin de la Forge   [series, complete]"
    for expected in ("series       Nains", "volume       1", "writer       Jarry, Nicolas",
                     "published    2015", "genres       Fantasy", "source       bedetheque #1", "file         "):
        assert expected in text
    assert "cbz, 3 pages" in text


def test_long_view_marks_partial_and_damaged_files(setup):
    code, text = run("--long", "partial:true")
    assert "[oneshot, matched, details not fetched (`bdnex import --fetch`)]" in text and "bib.cbz" in text


def test_long_json_has_the_complete_records(setup):
    code, text = run("--long", "--json", "year:2015")
    record = json.loads(text)[0]
    assert record["credits"] == {"Writer": ["Jarry, Nicolas"]} and record["genres"] == ["Fantasy"]
    assert record["file_records"][0]["path"].endswith("nains1.cbz") and record["extra"] == {}


def test_long_files_view(setup):
    code, text = run("--long", "--files")
    assert "album: Nains - T01 - Redwin de la Forge" in text and "album: (not imported)" in text
    assert "md5: " in text and "3 pages" in text


def test_long_with_count(setup):
    assert run("--long", "-c") == (0, "2\n")


def test_long_view_columns_never_run_together(tmp_path, monkeypatch, make_cbz):
    from bdnex.ui import describe
    album = {"kind": "oneshot", "series": None, "volume": None, "alternate_number": None, "title": "T",
             "credits": {"CoverArtist": ["Goux"], "Writer": ["W"]}, "genres": [], "source": "s", "source_id": "1",
             "extra": {}}
    text = describe.long(album, [])
    assert "coverartist  Goux" in text  # the longest label still leaves a gap


def test_complete_is_the_opposite_of_partial(setup):
    code, text = run("complete:false")
    assert code == 0 and "bibliomule" in text.lower() and "[not fetched]" in text
    assert run("complete:true")[1].strip() == run("partial:false")[1].strip()
    assert "bibliomule" not in run("complete:true")[1].lower()
