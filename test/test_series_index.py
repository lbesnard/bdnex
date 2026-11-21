"""The letter index: every series of the site by name (``bandes_dessinees_a.html``), read by ``bdnex init``."""

import io

from bdnex.importer.bedetheque import letters
from bdnex.importer.bedetheque.bedetheque import Bedetheque
from bdnex.library import Library
from helpers import FakeWeb

PAGE = open(__file__.replace("test_series_index.py", "bandes_dessinees_a.html"), encoding="utf-8").read()


def test_parse_letter_page():
    entries = letters.parse_letter_page(PAGE, "a")
    assert [e.series_id for e in entries] == ["47467", "1", "2", "3", "123456"]  # the duplicate and the author list are left out
    nains, aladdin, asterix, town, _ = entries
    assert nains.name == "Les Nains" and nains.country == "France" and nains.letter == "a"
    assert nains.url == "https://www.bedetheque.com/serie-47467-BD-Nains.html"
    assert aladdin.name == "Aladdin (Marvel - 2008)" and aladdin.title == "Aladdin" and aladdin.country == "USA"
    assert asterix.name == "L'Astérix & l'Obélix"
    assert town.title == "4☆Town" and town.country == "Japan"


def test_trailing_article_and_entities():
    assert letters.clean_title("Astérix &amp; l&#039;Obélix (L&#039;)") == "L'Astérix & l'Obélix"
    assert letters.clean_title("Nains (Les)") == "Les Nains"
    assert letters.clean_title("Spirou (2024)") == "Spirou (2024)"  # a year is not an article


def test_announced_count():
    assert letters.announced_count(PAGE) == 6
    assert letters.announced_count("<h1>Séries BD : 0 <small>5 514 séries</small></h1>") == 5514
    assert letters.announced_count("nothing") is None


def test_update_stores_the_index_and_searches_it(tmp_path):
    web = FakeWeb()
    with Library(str(tmp_path / "lib.db")) as library:
        source = Bedetheque(library, fetch=web, delay=(0, 0), sitemap_delay=(0, 0), sleep=lambda _: None)
        total = source.update_series_index()
        assert total == 5 + 26  # the real page, and one series for each other letter
        assert library.series_index_count("bedetheque") == total
        requests = len(web.calls)
        assert source.update_series_index() == total and len(web.calls) == requests  # stored pages are parsed again, not fetched
        from bdnex.importer.album_search import AlbumList

        class Stub:
            def catalogue(self):
                return []

            series_catalogue = source.series_catalogue

        hits, _ = AlbumList(Stub()).search_series("nains")
        assert hits[0][0].url.endswith("serie-47467-BD-Nains.html") and hits[0][0].name == "Les Nains"
        hits, _ = AlbumList(Stub()).search_series("aladin marvel")  # the disambiguation is searched too
        assert hits and hits[0][0].source_id == "1"


def test_init_reads_the_index_and_can_skip_it(tmp_path, monkeypatch):
    import yaml

    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"), "bedetheque": {"delay": [0, 0]}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    web = FakeWeb()
    import bdnex.importer.bedetheque as plugin

    monkeypatch.setattr(plugin, "http_fetch", web)
    monkeypatch.setattr(plugin, "build_fetcher", lambda settings: web)
    monkeypatch.setattr("bdnex.importer.bedetheque.bedetheque.uniform", lambda a, b: 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    from bdnex.ui import init_cmd

    out = io.StringIO()
    assert init_cmd.run(["--skip-series"], out=out, bar_stream=io.StringIO()) == 0
    assert "series in the index" not in out.getvalue() and not any("bandes_dessinees" in url for url in web.calls)
    out = io.StringIO()
    assert init_cmd.run(["--series-only"], out=out, bar_stream=io.StringIO()) == 0
    assert "31 series in the index" in out.getvalue() and "albums in the list" not in out.getvalue()
    with Library(str(tmp_path / "lib.sqlite"), readonly=True) as library:
        assert library.series_index_count("bedetheque") == 31


def test_review_search_lists_series_beside_albums(tmp_path):
    from bdnex.importer.source import Candidate
    from bdnex.ui.review_tui import SourceHub, fuzzy_search

    class Source:
        def catalogue(self):
            return [Candidate("9", "https://www.bedetheque.com/BD-Nains-Tome-1-Redwin-9.html", "nains 1", 1)]

        def series_catalogue(self):
            return [Candidate("47467", "https://www.bedetheque.com/serie-47467-BD-Nains.html", "Les Nains", None)]

    hub = SourceHub(Source())
    hub.ready.result()
    plain, _ = fuzzy_search(hub, "nains")
    assert [c.source_id for c, _ in plain] == ["9"]
    both, _ = fuzzy_search(hub, "nains", series=True)
    assert {c.source_id for c, _ in both} == {"9", "47467"}
    hub.close()
