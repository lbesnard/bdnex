import io
import os

import pytest
import yaml

import helpers
from bdnex.importer.bedetheque import Bedetheque
from bdnex.library import AlbumInfo, Library
from bdnex.ui import reparse_cmd

ONE_URL = "https://www.bedetheque.com/BD-Bibliomule-de-Cordoue-433432.html"
ONE_HTML = os.path.join(os.path.dirname(__file__), "bedetheque_oneshot_bibliomule.html")
SERIES_PAGE_URL = "https://m.bedetheque.com/serie-47467-BD-Nains.html"


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "c.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db),
                                    "bedetheque": {"fetcher": "helpers:recording_fetch"}}))  # would hit the network
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    helpers.FETCHED.clear()
    with Library(db) as lib:
        # an album stored by an older bdnex: no format, no edition details, and its page is in the cache
        lib.cache_put("bedetheque", "album", ONE_URL, open(ONE_HTML, "rb").read())
        old = lib.add_album(AlbumInfo("bedetheque", "433432", "La bibliomule de Cordoue", url=ONE_URL, language="fr",
                                      credits={"Writer": ["Lupano, Wilfrid"]}, genres=["Satire"]))
    return db, old


def run(*argv):
    out = io.StringIO()
    code = reparse_cmd.run(list(argv), out=out)
    return code, out.getvalue()


def stored(db, album_id):
    with Library(db) as lib:
        return lib.get_album(album_id)


def test_preview_lists_the_new_details_and_changes_nothing(env):
    db, album_id = env
    before = stored(db, album_id)
    code, text = run("-n")
    assert code == 0 and "would update" in text and "La bibliomule de Cordoue" in text
    assert "format     None → 'Autre format'" in text and "Other      - → Ferté, Ségolène" in text
    assert "new details  " in text and "edition_info" in text and "1 would be updated" in text
    assert stored(db, album_id) == before


def test_reparse_fills_in_what_the_page_always_had(env):
    db, album_id = env
    code, text = run()
    assert code == 0 and "1 updated" in text and "Run `bdnex write`" in text
    album = stored(db, album_id)
    assert album["format"] == "Autre format" and album["credits"]["Other"] == ["Ferté, Ségolène"]
    assert "Pascal Buresi" in album["extra"]["edition_info"] and album["extra"]["edition_fields"]["Poids"] == "1252 g"
    assert album["credits"]["Writer"] == ["Lupano, Wilfrid"]  # what was there stays


def test_a_second_run_has_nothing_to_do(env):
    run()
    code, text = run()
    assert code == 0 and "0 updated, 1 unchanged" in text and "Run `bdnex write`" not in text


def test_it_never_uses_the_network_even_with_a_configured_fetcher(env):
    run()
    run("-n")
    assert helpers.FETCHED == []  # the fetcher in the configuration was never called


def test_values_typed_by_hand_survive_where_the_page_is_silent(env):
    db, album_id = env
    with Library(db) as lib:
        lib.add_credit(album_id, "Inker", "Typed By Hand")  # the page names no inker
        lib.add_credit(album_id, "Colorist", "Typed By Hand")  # the page names a colorist
    run()
    credits = stored(db, album_id)["credits"]
    assert credits["Inker"] == ["Typed By Hand"]  # kept: the page says nothing about inkers
    assert credits["Colorist"] == ["Bouchard, Christophe"]  # replaced: the page is authoritative for a role it lists


def test_albums_without_a_stored_page_are_reported_not_fetched(env):
    db, album_id = env
    with Library(db) as lib:
        lib.add_album(AlbumInfo("bedetheque", "999", "Never fetched", url="https://www.bedetheque.com/BD-X-999.html",
                                extra={"partial": True}))
    code, text = run()
    assert code == 0 and "no stored page  Never fetched" in text
    assert "1 updated" in text and "1 without a stored page" in text and helpers.FETCHED == []


def test_a_query_limits_the_albums(env):
    db, album_id = env
    with Library(db) as lib:
        lib.add_album(AlbumInfo("bedetheque", "999", "Other", url="https://www.bedetheque.com/BD-X-999.html"))
    code, text = run("title:cordoue")
    assert "1 album(s): 1 updated" in text


def test_series_album_with_its_series_page_missing_does_not_fail_or_fetch(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr("bdnex.importer.bedetheque.bedetheque.uniform", lambda a, b: waits.append((a, b)) or 0)
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "c.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db),
                                    "bedetheque": {"fetcher": "helpers:recording_fetch"}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    helpers.FETCHED.clear()
    url = "https://m.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html"
    with Library(db) as lib:
        lib.cache_put("bedetheque", "album", url, open(helpers.os.path.join(os.path.dirname(__file__), "mobile_redwin.html"), "rb").read())
        lib.add_album(AlbumInfo("bedetheque", "245127", "Redwin de la Forge", url=url))
    code, text = run()  # the series page was never stored
    assert code == 0 and "1 updated" in text and helpers.FETCHED == []
    assert set(waits) <= {(0, 0)}  # offline: no 3-10 s politeness wait for a request that is never made


def test_a_page_that_no_longer_parses_is_reported_and_the_rest_continue(env, monkeypatch):
    db, album_id = env
    with Library(db) as lib:
        lib.cache_put("bedetheque", "album", "https://www.bedetheque.com/BD-Broken-5.html", b"<html></html>")
        lib.add_album(AlbumInfo("bedetheque", "5", "Broken", url="https://www.bedetheque.com/BD-Broken-5.html"))
    original = Bedetheque.album_info

    def flaky(self, url, recache=False):
        if "Broken" in url:
            raise ValueError("cannot read this page")
        return original(self, url, recache)

    monkeypatch.setattr(Bedetheque, "album_info", flaky)
    code, text = run()
    assert code == 1 and "failed  Broken" in text and "cannot read this page" in text and "1 updated" in text


def test_errors(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "c.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "none.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert run()[0] == 1 and "bdnex import" in capsys.readouterr().err
    conf.write_text("config_version: 2\noops: 1\n")
    assert run()[0] == 1 and "oops" in capsys.readouterr().err


def test_bad_query_and_unknown_source(env, monkeypatch, capsys):
    db, _ = env
    assert run("year:abc")[0] == 2 and "bad query" in capsys.readouterr().err
    conf = os.environ["BDNEX_CONFIG"]
    cfg = yaml.safe_load(open(conf))
    cfg["source"] = "nope"
    open(conf, "w").write(yaml.safe_dump(cfg))
    assert run()[0] == 1 and "unknown source 'nope'" in capsys.readouterr().err


def test_main_dispatches_reparse(monkeypatch, tmp_path):
    from bdnex import ui
    conf = tmp_path / "c.yaml"
    conf.write_text("config_version: 2\ncollection_path: ~/c\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    seen = []
    monkeypatch.setattr("bdnex.ui.reparse_cmd.run", lambda argv, out=None: seen.append(argv) or 0)
    with pytest.raises(SystemExit):
        ui.main(["reparse", "-n"])
    assert seen == [["-n"]]


def test_a_change_is_drawn_old_red_new_green_and_plain_without_a_terminal():
    from bdnex.ui.reparse_cmd import format_album, format_change

    line = "title: 'A.D Grand-Rivière' -> 'Terre d election'"
    assert format_change(line, False) == "title      'A.D Grand-Rivière' → 'Terre d election'"
    drawn = format_change(line, True)
    assert "\x1b[31m'A.D Grand-Rivière'" in drawn and "\x1b[32m'Terre d election'" in drawn
    assert "\x1b[32mTitre" in format_change("new details: Titre", True)
    assert "\x1b[33mTitre" in format_change("updated details: Titre", True)
    block = format_album("A - T01", [line], True, 152, 313, False)
    assert block.startswith("\n[152/313] ● would update  A - T01") and "\n    title " in block


def test_a_series_page_is_read_once_for_all_the_albums_of_the_series(tmp_path, monkeypatch):
    from bdnex.importer.bedetheque import parser

    web = helpers.FakeWeb()
    with Library(tmp_path / "l.sqlite") as lib:
        source = Bedetheque(lib, fetch=web, sleep=lambda s: None)
        first = helpers.ALBUM_URL
        lib.cache_put("bedetheque", "album", first, web(first))
        lib.cache_put("bedetheque", "album", first + "?copy", web(first))  # a second album of the same series
        calls = []
        real = parser.parse_series_details
        monkeypatch.setattr(parser, "parse_series_details", lambda html: calls.append(1) or real(html))
        one, two = source.album_info(first), source.album_info(first + "?copy")
        assert len(calls) == 1 and one.series.extra == two.series.extra and one.series.extra  # parsed once, both albums have it
        one.series.extra["x"] = 1  # what a caller does to its copy must not reach the next album
        assert "x" not in source.album_info(first).series.extra


def test_pages_read_in_several_processes_give_what_one_process_gives(env):
    from bdnex.config import load_config

    db, _ = env
    with Library(db) as lib:
        urls = []
        for number in range(6):  # six albums (the same page under six addresses), so that there are enough tasks to share
            url = f"{ONE_URL}?n={number}"
            lib.cache_put("bedetheque", "album", url, open(ONE_HTML, "rb").read())
            urls.append(url)
    settings = load_config().settings
    offline = settings.model_copy(update={"bedetheque": settings.bedetheque.model_copy(
        update={"fetcher": reparse_cmd.OFFLINE, "delay": (0, 0), "sitemap_delay": (0, 0)})})
    groups = [[url] for url in urls]
    alone = reparse_cmd.read_groups(settings, offline, groups, 1)
    several = reparse_cmd.read_groups(settings, offline, groups, 3)
    assert sorted(alone) == sorted(several) == sorted(urls)
    assert all(info is not None and not error for info, error in several.values())
    assert [alone[u][0] for u in urls] == [several[u][0] for u in urls]


def test_the_order_of_the_names_of_a_credit_is_not_a_change(tmp_path):
    with Library(tmp_path / "l.sqlite") as lib:
        lib.add_album(AlbumInfo("bedetheque", "1", "A", credits={"Colorist": ["Beuzelin, Boris", "Marty, Lionel"]}))
        again = AlbumInfo("bedetheque", "1", "A", credits={"Colorist": ["Marty, Lionel", "Beuzelin, Boris"]})
        assert lib.preview_merge(again) == []
