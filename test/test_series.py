"""Series metadata: the albums a series page lists, kept in the series table, and `bdnex series`."""

import io
import json
import os
import sqlite3

import pytest
import yaml

from bdnex.importer.bedetheque import Bedetheque, parser
from bdnex.library import AlbumInfo, Library, SeriesInfo, SCHEMA_VERSION
from bdnex.ui import series_cmd
from helpers import ALBUM_URL, SERIES_URL, FakeWeb

HERE = os.path.dirname(__file__)
NAINS_PAGE = open(os.path.join(HERE, ".local/share/bdnex/bedetheque/series_html/serie-47467-BD-Nains.html"),
                  encoding="utf-8", errors="replace").read()


# ---------------------------------------------------------------------------- the parser
def test_a_series_page_lists_its_albums():
    details = parser.parse_series_details(NAINS_PAGE)
    albums = details["albums"]
    assert details["summary"].startswith("Redwin, fils d'Ulrog")
    assert len(albums) == 26 and [a["volume"] for a in albums[:3]] == [1, 2, 3]
    assert albums[0] == {"source_id": "245127", "title": "Redwin de la Forge",
                         "url": "https://www.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html", "volume": 1}
    assert albums[-1]["volume"] is None and albums[-1]["title"].startswith("INT01")  # the integral is not a numbered volume
    assert all(a["url"].startswith("https://www.bedetheque.com/") for a in albums)  # not the mobile site


def test_links_to_other_series_are_left_out():
    html = """<html><body><h1>Foo</h1>
      <a href="https://www.bedetheque.com/BD-Foo-Tome-1-Un-111.html">1</a>
      <a href="https://www.bedetheque.com/BD-Foo-Tome-2-Deux-112.html">2</a>
      <a href="https://www.bedetheque.com/BD-Foo-HS1-Special-113.html">hs</a>
      <a href="https://www.bedetheque.com/BD-Bar-Tome-1-Autre-221.html">similar series</a>
      <a href="https://www.bedetheque.com/BD-Foo-Tome-1-Un-111.html">again</a>
      <a href="https://www.bedetheque.com/serie-9-BD-Foo.html">the series itself</a></body></html>"""
    albums = parser.parse_series_details(html)["albums"]
    assert [(a["source_id"], a["volume"]) for a in albums] == [("111", 1), ("112", 2), ("113", None)]  # no duplicate, no Bar


def test_a_page_without_album_links_gives_only_what_it_has():
    assert parser.parse_series_details("<html><body><p>nothing</p></body></html>") == {}


# ----------------------------------------------------------------------- the library
def test_schema_has_the_series_extra_column():
    lib = Library(":memory:")
    assert lib.schema_version == SCHEMA_VERSION >= 4
    assert "extra" in [r[1] for r in lib.conn.execute("PRAGMA table_info(series)")]


def test_series_extra_is_merged_and_a_summary_is_not_erased():
    lib = Library(":memory:")
    first = lib.add_series(SeriesInfo("b", "1", "Nains", summary="Résumé", url="https://x/serie-1.html", extra={"albums": [1], "status": "ongoing"}))
    again = lib.add_series(SeriesInfo("b", "1", "Les Nains", extra={"albums": [1, 2]}))  # a record that lacks the summary and address
    assert first == again
    row = lib.conn.execute("SELECT * FROM series").fetchone()
    assert row["name"] == "Les Nains" and row["summary"] == "Résumé" and row["url"] == "https://x/serie-1.html"
    assert json.loads(row["extra"]) == {"albums": [1, 2], "status": "ongoing"}  # new values win, others stay


def test_reading_an_album_stores_the_albums_of_its_series():
    web = FakeWeb()
    lib = Library(":memory:")
    source = Bedetheque(lib, fetch=web, sleep=lambda s: None)
    info = source.album_info(ALBUM_URL)
    assert len(info.series.extra["albums"]) == 26 and info.series.summary
    lib.add_album(info)
    row = lib.conn.execute("SELECT * FROM series WHERE source_id = '47467'").fetchone()
    assert len(json.loads(row["extra"])["albums"]) == 26 and row["summary"] == info.series.summary
    assert SERIES_URL in web.calls  # the series page is the one that was already being read: no new request


def test_the_overview_says_what_is_owned_and_what_is_missing(make_cbz):
    lib = Library(":memory:")
    albums = [{"source_id": str(n), "volume": n, "title": f"Titre {n}", "url": f"https://x/BD-Foo-Tome-{n}-{n}.html"} for n in (1, 2, 3, 4)]
    series = SeriesInfo("b", "7", "Foo", extra={"albums": albums})
    for n in (1, 2):
        album = lib.add_album(AlbumInfo("b", str(n), f"Titre {n}", series=series, volume=n))
        lib.add_file(make_cbz(f"foo{n}.cbz"), album)
    lib.add_album(AlbumInfo("b", "3", "Titre 3", series=series, volume=3))  # known, but no file for it
    (row,) = lib.series_overview()
    assert (row["owned"], row["known"]) == (2, 4) and [a["volume"] for a in row["missing"]] == [3, 4]


def test_a_series_whose_page_was_not_read_has_no_list_to_compare_with(make_cbz):
    lib = Library(":memory:")
    album = lib.add_album(AlbumInfo("b", "1", "T", series=SeriesInfo("b", "slug:foo", "Foo"), volume=1))
    lib.add_file(make_cbz("a.cbz"), album)
    (row,) = lib.series_overview()
    assert row["owned"] == 1 and row["known"] is None and row["missing"] is None


def test_the_overview_filters_by_name_and_skips_empty_series(make_cbz):
    lib = Library(":memory:")
    lib.add_series(SeriesInfo("b", "1", "Nothing linked"))
    a = lib.add_album(AlbumInfo("b", "1", "T", series=SeriesInfo("b", "2", "Les Cités Obscures"), volume=1))
    lib.add_file(make_cbz("a.cbz"), a)
    assert [r["name"] for r in lib.series_overview()] == ["Les Cités Obscures"]
    assert [r["name"] for r in lib.series_overview(["CITES", "obscures"])] == ["Les Cités Obscures"]  # case and accents
    assert lib.series_overview(["zzz"]) == []


# ----------------------------------------------------------------------- the command
@pytest.fixture
def library_with_series(tmp_path, monkeypatch, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))

    def listed(ids):
        return [{"source_id": str(i), "volume": i % 10, "title": f"Titre {i % 10}",
                 "url": f"https://www.bedetheque.com/BD-Foo-Tome-{i % 10}-{i}.html"} for i in ids]

    with Library(tmp_path / "lib.sqlite") as lib:
        series = {"Complete": SeriesInfo("b", "1", "Complete", extra={"albums": listed([11])}),
                  "Foo": SeriesInfo("b", "2", "Foo", extra={"albums": listed([21, 22, 23])}),
                  "Bar": SeriesInfo("b", "slug:bar", "Bar")}  # its page was never read
        for name, owned in (("Complete", 11), ("Foo", 21), ("Bar", 31)):
            album = lib.add_album(AlbumInfo("b", str(owned), "T", series=series[name], volume=owned % 10))
            lib.add_file(make_cbz(f"{name}.cbz"), album)
    return tmp_path


def run(*argv):
    out = io.StringIO()
    return series_cmd.run(list(argv), out=out), out.getvalue()


def test_series_command_summary(library_with_series):
    code, text = run()
    lines = text.splitlines()
    assert code == 0
    assert "Bar  1 album(s)  (the series' own album list is not known yet)" in lines
    assert "Complete  1 of 1 albums  ·  complete" in lines
    assert "Foo  1 of 3 albums  ·  missing 2" in lines


def test_series_command_missing_and_long(library_with_series):
    code, text = run("--missing", "--long")
    assert text.splitlines()[0] == "Foo  1 of 3 albums  ·  missing 2"
    assert "    T02  Titre 2  https://www.bedetheque.com/BD-Foo-Tome-2-22.html" in text and "Complete" not in text and "Bar" not in text


def test_series_command_name_filter_and_json(library_with_series):
    assert run("foo")[1].count("\n") == 1
    assert run("nothing like it")[1].strip() == "No series with that name."
    code, text = run("--json", "foo")
    (row,) = json.loads(text)
    assert row["name"] == "Foo" and row["owned"] == 1 and row["known"] == 3 and len(row["missing"]) == 2


def test_series_command_without_a_library(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "none.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert series_cmd.run([], out=io.StringIO()) == 1 and "No library yet" in capsys.readouterr().err
