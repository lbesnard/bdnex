import os

import pytest

from bdnex.importer.bedetheque import Bedetheque, parser, sitemap
from bdnex.library import Library

TEST_ROOT = os.path.dirname(os.path.dirname(__file__))
BDGEST = os.path.join(TEST_ROOT, ".local/share/bdnex/bedetheque")
ALBUM_HTML = os.path.join(TEST_ROOT, "mobile_redwin.html")
SERIES_HTML = os.path.join(BDGEST, "series_html/serie-47467-BD-Nains.html")
SITEMAPS = os.path.join(BDGEST, "sitemaps")
ALBUM_URL = "https://m.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html"
SERIES_URL = "https://m.bedetheque.com/serie-47467-BD-Nains.html"


def read(path, mode="rb"):
    with open(path, mode) as fh:
        return fh.read()


class FakeWeb:
    """Replaces the network: serves fixtures and counts requests."""

    def __init__(self):
        self.calls = []
        self.pages = {ALBUM_URL: read(ALBUM_HTML), SERIES_URL: read(SERIES_HTML),
                      "https://www.bedetheque.com/media/Couvertures/Couv_245127.jpg": b"JPEGDATA"}
        for url in sitemap.sitemap_urls():
            self.pages[url] = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'
        for name in os.listdir(SITEMAPS):
            self.pages["https://www.bedetheque.com/" + name] = read(os.path.join(SITEMAPS, name))

    def __call__(self, url):
        self.calls.append(url)
        return self.pages[url]


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def lib():
    with Library(":memory:") as library:
        yield library


@pytest.fixture
def bdt(lib, web):
    sleeps = []
    plugin = Bedetheque(lib, fetch=web, sleep=sleeps.append)
    plugin.sleeps = sleeps
    return plugin


# ------------------------------------------------------------------ sitemap
def test_sitemap_urls():
    urls = sitemap.sitemap_urls()
    assert len(urls) == 47
    assert urls[0] == "https://www.bedetheque.com/albums_1_10000_map.xml"
    assert urls[5] == "https://www.bedetheque.com/albums_50001_60000_map.xml"


def test_remove_common_words():
    assert sitemap.remove_common_words("la MAisOn du lAc") == "maison lac"


def test_normalise_slug_and_id():
    assert sitemap.normalise_slug(ALBUM_URL) == "nains 1 redwin forge"
    assert sitemap.source_id_from_url(ALBUM_URL) == "245127"
    assert sitemap.source_id_from_url("https://x/nothing") is None


def test_parse_sitemap_fixture():
    cands = sitemap.parse_sitemap(read(os.path.join(SITEMAPS, "albums_240001_250000_map.xml")))
    assert cands[0].source_id == "240001"
    assert cands[0].url.startswith("https://m.bedetheque.com/BD-Mimura")
    assert cands[0].name == "mimura kataguri days of days of mimura kataguri"


# ------------------------------------------------------------------- caching
def test_update_sitemaps_fetches_once(bdt, web, lib):
    assert bdt.update_sitemaps() == 47
    assert len(bdt.sleeps) == 47
    assert bdt.sitemaps_cached() == 47
    assert bdt.update_sitemaps() == 0  # all cached
    assert bdt.update_sitemaps(recache=True) == 47


def test_candidates_need_sitemaps(bdt):
    with pytest.raises(FileNotFoundError):
        bdt.candidates("nains")


@pytest.mark.parametrize("name", ["Nains-Redwin de la forge", "Redwin de la forge"])
def test_candidates_rank_expected_album_first(bdt, name):
    bdt.update_sitemaps()
    cand, score = bdt.candidates(name)[0]
    assert cand.url == ALBUM_URL and score > 30


def test_candidates_limit(bdt):
    bdt.update_sitemaps()
    assert len(bdt.candidates("nains", limit=3)) == 3


# ------------------------------------------------------------------- parsing
def test_parse_album_info(bdt):
    info = bdt.album_info(ALBUM_URL)
    assert info.source == "bedetheque" and info.source_id == "245127"
    assert info.title == "Redwin de la Forge"
    assert info.kind == "series" and info.series.name == "Nains"
    assert info.series.source_id == "47467"
    assert info.volume == 1 and info.pages == 56
    assert info.publisher == "Soleil Productions" and info.isbn == "978-2-302-04644-3"
    assert (info.year, info.month, info.day) == (2015, 6, 3)
    assert info.rating == 4.25
    assert info.credits["Writer"] == ["Jarry, Nicolas"]
    assert info.credits["Penciller"] == ["Goux, Pierre-Denis"]
    assert info.genres == ["Fantasy"]
    assert info.summary.startswith("Redwin,")  # series abstract prepended
    assert info.cover_url.endswith("Couv_245127.jpg")


def test_album_info_cached_second_time(bdt, web):
    bdt.album_info(ALBUM_URL)
    n = len(web.calls)
    assert n == 2  # album + series
    bdt.album_info(ALBUM_URL)
    assert len(web.calls) == n
    bdt.album_info(ALBUM_URL, recache=True)
    assert len(web.calls) == n + 2


def test_album_info_survives_missing_series_page(lib, web):
    del web.pages[SERIES_URL]
    info = Bedetheque(lib, fetch=web, sleep=lambda s: None).album_info(ALBUM_URL)
    assert info.title == "Redwin de la Forge"
    assert info.series.summary is None


def test_cover_cached(bdt, web):
    info = bdt.album_info(ALBUM_URL)
    assert bdt.cover(info) == b"JPEGDATA"
    calls = len(web.calls)
    assert bdt.cover(info) == b"JPEGDATA" and len(web.calls) == calls


def test_info_stores_in_library(bdt, lib):
    album_id = lib.add_album(bdt.album_info(ALBUM_URL))
    album = lib.get_album(album_id)
    assert album["series"] == "Nains" and album["credits"]["Writer"] == ["Jarry, Nicolas"]


# -------------------------------------------------------- build_album_info
def base_fields(**kw):
    f = {"album_url": "https://m.bedetheque.com/BD-X-1.html", "Titre": "T", "Identifiant": "1"}
    f.update(kw)
    return f


def test_oneshot_when_no_tome():
    info = parser.build_album_info(base_fields(**{"Série": "Seul"}))
    assert info.kind == "oneshot" and info.series is None


def test_hors_serie_is_series_with_alternate_number():
    info = parser.build_album_info(base_fields(**{"Série": "S", "Tome": "Hors-série"}),
                                   "https://m.bedetheque.com/serie-9-BD-S.html")
    assert info.kind == "series" and info.alternate_number == "Hors-série" and info.volume is None
    assert info.series.source_id == "9"


def test_integrale_is_alternate_number_and_dates_parsed():
    info = parser.build_album_info(base_fields(**{"Série": "S", "Tome": "INT 3",
                                                  "Dépot_légal": "(Parution le 15/02/2010)"}))
    assert info.volume is None and info.alternate_number == "INT 3"
    assert (info.year, info.month, info.day) == (2010, 2, 15)


def test_composite_tome_takes_last_number():
    info = parser.build_album_info(base_fields(**{"Série": "S", "Tome": "2 3"}))
    assert info.volume == 3


def test_multiple_authors_and_genres_split():
    info = parser.build_album_info(base_fields(Scénario="A, B\nC", Style="Aventure, Humour"))
    assert info.credits["Writer"] == ["A, B", "C"]
    assert info.genres == ["Aventure", "Humour"]


def test_bad_date_ignored():
    info = parser.build_album_info(base_fields(**{"Dépot_légal": "n/a"}))
    assert info.year is None
