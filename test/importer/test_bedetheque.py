import os

import pytest

from bdnex.importer.bedetheque import Bedetheque, parser, sitemap
from bdnex.library import Library
from helpers import FakeWeb, COVER_BYTES

TEST_ROOT = os.path.dirname(os.path.dirname(__file__))
BDGEST = os.path.join(TEST_ROOT, ".local/share/bdnex/bedetheque")
ALBUM_HTML = os.path.join(TEST_ROOT, "mobile_redwin.html")
SERIES_HTML = os.path.join(BDGEST, "series_html/serie-47467-BD-Nains.html")
SITEMAPS = os.path.join(BDGEST, "sitemaps")
ALBUM_URL = "https://m.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html"  # legacy mobile layout
WWW_URL = "https://www.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html"
ONESHOT_URL = "https://www.bedetheque.com/BD-Bibliomule-de-Cordoue-433432.html"
ONESHOT_HTML = os.path.join(TEST_ROOT, "bedetheque_oneshot_bibliomule.html")
NEW_SITEMAP = os.path.join(TEST_ROOT, "sitemap_430001_440000_new_format.xml")
SERIES_URL = "https://m.bedetheque.com/serie-47467-BD-Nains.html"


def read(path, mode="rb"):
    with open(path, mode) as fh:
        return fh.read()


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
    urls = sitemap.sitemap_urls(47)
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
    assert cands[0].url.startswith("https://www.bedetheque.com/BD-Mimura")
    assert cands[0].volume is None and cands[0].kind == "oneshot"
    assert cands[0].name == "mimura kataguri days of days of mimura kataguri"


# ------------------------------------------------------------------- caching
def test_update_sitemaps_fetches_once(bdt, web, lib):
    assert bdt.update_sitemaps() == 27  # 26 files + the empty one that ends the list
    assert len(bdt.sleeps) == 27
    assert bdt.sitemaps_cached() == 26
    assert bdt.update_sitemaps() == 0  # all cached
    assert bdt.update_sitemaps(recache=True) == 27


def test_candidates_need_sitemaps(bdt):
    with pytest.raises(FileNotFoundError):
        bdt.candidates("nains")


@pytest.mark.parametrize("name", ["Nains-Redwin de la forge", "Redwin de la forge"])
def test_candidates_rank_expected_album_first(bdt, name):
    bdt.update_sitemaps()
    cand, score = bdt.candidates(name)[0]
    assert cand.url == WWW_URL and score > 30
    assert cand.volume == 1 and cand.kind == 'series'


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


def test_a_cover_is_not_kept_in_the_library_cache(bdt, web, lib):
    info = bdt.album_info(ALBUM_URL)
    assert bdt.cover_image(info.cover_url) == COVER_BYTES  # the importer saves it as a file instead
    assert lib.cache_get("bedetheque", "cover", info.cover_url) is None


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


# ------------------------------------------------------------ anti-bot pages
def test_challenge_page_detected():
    from bdnex.importer.bedetheque.bedetheque import looks_blocked
    page = b"<html><head><title>Just a moment...</title>challenges.cloudflare.com"
    assert looks_blocked(403, page) and looks_blocked(200, page)
    assert not looks_blocked(200, b"<html><title>Nains</title></html>")
    assert not looks_blocked(404, b"not found")


def test_blocked_response_raises_and_is_not_cached(lib):
    import requests
    from unittest.mock import patch, MagicMock
    from bdnex.importer.bedetheque.bedetheque import BlockedError, http_fetch

    resp = MagicMock(status_code=403, content=b"<title>Just a moment...</title>")
    with patch.object(requests, "get", return_value=resp):
        plugin = Bedetheque(lib, fetch=http_fetch, sleep=lambda s: None)
        with pytest.raises(BlockedError):
            plugin.album_info(ALBUM_URL)
    assert lib.cache_get("bedetheque", "album", ALBUM_URL) is None


# --------------------------------------------- current sitemap + desktop layout
def test_new_format_sitemap_has_no_mobile_links():
    cands = sitemap.parse_sitemap(read(NEW_SITEMAP))
    assert len(cands) == 5
    one = [c for c in cands if c.source_id == "433432"][0]
    assert one.url == ONESHOT_URL and one.volume is None and one.kind == "oneshot"
    assert one.name == "bibliomule cordoue"


def test_volume_from_slug():
    assert sitemap.volume_from_url(WWW_URL) == 1
    assert sitemap.volume_from_url("https://x/BD-Asterix-Tome-12-Titre-9.html") == 12
    assert sitemap.volume_from_url(ONESHOT_URL) is None


def test_empty_sitemap_detected():
    assert sitemap.is_empty_sitemap(b'<urlset xmlns="x">\n</urlset>')
    assert not sitemap.is_empty_sitemap(read(NEW_SITEMAP))


def test_oneshot_desktop_page_parsed(lib, web):
    html = open(ONESHOT_HTML, encoding="utf-8").read()
    info = Bedetheque(lib, fetch=web, sleep=lambda s: None).album_info_from_html(html, ONESHOT_URL)
    assert info.kind == "oneshot" and info.series is None
    assert info.source_id == "433432" and info.title == "La bibliomule de Cordoue"
    assert info.publisher == "Dargaud" and info.isbn == "9782505078647"
    assert (info.year, info.month, info.day) == (2021, 11, 26) and info.pages == 251
    assert info.language == "fr" and info.rating == 4.2
    assert info.credits["Writer"] == ["Lupano, Wilfrid"]
    assert info.credits["Penciller"] == ["Chemineau, Léonard"]
    assert info.credits["Colorist"] == ["Bouchard, Christophe"]
    assert info.genres == ["Conte ou légende", "Satire"]
    assert info.extra["Origine"] == "Franco-Belge"
    assert info.extra["autres_credits"] == {"Autres": ["Ferté, Ségolène"]}
    assert info.summary.startswith("Califat")
    assert info.cover_url == "https://www.bedetheque.com/media/Couvertures/Couv_433432.jpg"


def test_saved_page_is_cached_for_offline_reuse(lib, web):
    html = open(ONESHOT_HTML, encoding="utf-8").read()
    plugin = Bedetheque(lib, fetch=web, sleep=lambda s: None)
    plugin.album_info_from_html(html, ONESHOT_URL)
    web.calls.clear()
    assert plugin.album_info(ONESHOT_URL).source_id == "433432"
    assert web.calls == []


# ------------------------------------------------- delays and progress
def test_sitemaps_use_their_own_short_delay_pages_the_long_one(lib, web):
    sleeps = []
    plugin = Bedetheque(lib, fetch=web, sleep=sleeps.append, delay=(5, 5), sitemap_delay=(0.1, 0.1))
    plugin.update_sitemaps()
    assert set(sleeps) == {0.1}  # 27 sitemap requests, none of them waits the page delay
    sleeps.clear()
    plugin.album_info(ALBUM_URL)
    assert set(sleeps) == {5}  # album + series pages


def test_update_sitemaps_reports_progress(lib, web):
    messages = []
    Bedetheque(lib, fetch=web, sleep=lambda s: None).update_sitemaps(progress=messages.append)
    assert len(messages) == 27 and messages[0] == "  sitemap 1: albums_1_10000_map.xml"


# ------------------------------------------- everything the desktop page says
EDITION_PAGE = """
<html><body><section class="bdt-ah"><h1><a href="https://www.bedetheque.com/serie-1-BD-S.html">Série S</a></h1>
<div class="bdt-ah-by"><span itemprop="publisher">Dargaud</span></div></section>
<article class="bdt-edition bdt-edition--detail"><div class="bdt-edition-body"><dl>
<dt>Titre</dt><dd>Un titre</dd><dt>Traduction</dt><dd>Maubille, Geneviève</dd>
<dt>Autres</dt><dd>Ferté, Ségolène</dd><dt>Format</dt><dd>Grand format</dd>
<dt>Poids</dt><dd>670 g</dd><dt>Achevé impr.</dt><dd>10/2021</dd><dt>Autres infos</dt><dd></dd></dl>
<div class="bdt-note"><div><b>Info édition</b>Noté "Première   édition".
Couverture toilée.</div></div></div></article></body></html>"""


def test_edition_table_note_and_credits_are_read():
    fields, _ = parser.parse_album_page(EDITION_PAGE, "https://www.bedetheque.com/BD-S-1.html")
    assert fields["edition_fields"]["Poids"] == "670 g" and "Autres infos" not in fields["edition_fields"]  # empty rows dropped
    assert fields["Format"] == "Grand format"
    assert fields["edition_info"] == 'Noté "Première édition". Couverture toilée.'  # label removed, spaces folded
    info = parser.build_album_info(fields)
    assert info.format == "Grand format"
    assert info.credits["Translator"] == ["Maubille, Geneviève"] and info.credits["Other"] == ["Ferté, Ségolène"]
    assert info.extra["edition_info"].startswith('Noté "Première édition"')


def test_no_edition_section_is_fine():
    fields, _ = parser.parse_album_page("<html><section class='bdt-ah'><h1>T</h1></section></html>", "https://x/BD-T-1.html")
    assert "edition_fields" not in fields and "edition_info" not in fields
    info = parser.build_album_info(fields)
    assert info.format is None and info.credits == {}


def test_real_oneshot_page_details():
    html = open(ONESHOT_HTML, encoding="utf-8").read()
    fields, _ = parser.parse_album_page(html, ONESHOT_URL)
    info = parser.build_album_info(fields)
    assert info.format == "Autre format" and info.extra["edition_fields"]["Poids"] == "1252 g"
    assert "Pascal Buresi" in info.extra["edition_info"] and "210 x 283 mm" in info.extra["edition_info"]
    assert info.extra["rating_votes"] == 90
    assert info.extra["image_urls"] == {
        "plates": ["https://www.bedetheque.com/media/Planches/PlancheA_433432.jpg"],
        "versos": ["https://www.bedetheque.com/media/Versos/Verso_433432.jpg"]}
    assert info.credits["Other"] == ["Ferté, Ségolène"] and info.credits["Writer"] == ["Lupano, Wilfrid"]


def test_the_edition_note_reaches_comicinfo_notes():
    from bdnex.organizer import comicinfo
    fields, _ = parser.parse_album_page(EDITION_PAGE, "https://www.bedetheque.com/BD-S-1.html")
    info = parser.build_album_info(fields)
    album = {"title": "T", "series": None, "kind": "oneshot", "credits": {}, "genres": [], "isbn": "9782505078647",
             "format": info.format, "extra": info.extra}
    notes = comicinfo.album_to_fields(album)["Notes"]
    assert notes.splitlines() == ["ISBN: 9782505078647", 'Édition: Noté "Première édition". Couverture toilée.']
    assert comicinfo.build_xml(comicinfo.album_to_fields(album))  # still valid against the schema


def _album_urls(count):
    return [f"https://www.bedetheque.com/BD-Serie-Tome-{n}-Titre-{1000 + n}.html" for n in range(1, count + 1)]


def test_prefetch_fetches_several_pages_at_once_and_keeps_the_order(lib):
    import threading
    import time
    state = {"now": 0, "most": 0}
    lock = threading.Lock()

    def fetch(url):
        with lock:
            state["now"] += 1
            state["most"] = max(state["most"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return b"<html>" + url.encode() + b"</html>"

    source = Bedetheque(lib, fetch=fetch, sleep=lambda s: None, parallel=3)
    urls = _album_urls(7)
    results = list(source.prefetch(urls, 3))
    assert [url for url, _ in results] == urls and all(error is None for _, error in results)
    assert 2 <= state["most"] <= 3  # several at once, never more than asked
    assert all(lib.cache_get("bedetheque", "album", url) is not None for url in urls)  # stored by the caller's thread
    state["most"] = 0
    assert [e for _, e in source.prefetch(urls, 3)] == [None] * 7 and state["most"] == 0  # all cached: nothing fetched


def test_prefetch_reports_a_failed_page_and_goes_on(lib):
    urls = _album_urls(5)

    def fetch(url):
        if "Tome-3-" in url:
            raise OSError("endpoint down")
        return b"<html>ok</html>"

    source = Bedetheque(lib, fetch=fetch, sleep=lambda s: None, parallel=2)
    results = dict(source.prefetch(urls, 2))
    assert isinstance(results[urls[2]], OSError) and [results[u] for u in urls if u != urls[2]] == [None] * 4
    assert lib.cache_get("bedetheque", "album", urls[2]) is None and lib.cache_get("bedetheque", "album", urls[3]) is not None


def test_closing_prefetch_early_stops_asking_for_more_pages(lib):
    fetched = []
    source = Bedetheque(lib, fetch=lambda url: fetched.append(url) or b"<html>x</html>", sleep=lambda s: None)
    stream = source.prefetch(_album_urls(40), 2)
    next(stream)
    stream.close()
    assert len(fetched) < 40  # the window ahead is small, and the rest was cancelled


def test_prefetch_lets_a_display_follow_what_is_being_fetched(lib):
    import time
    ticks, seen = [], []
    source = Bedetheque(lib, fetch=lambda url: (time.sleep(0.9), b"<html>x</html>")[1], sleep=lambda s: None, parallel=3)
    urls = _album_urls(3)
    for url, error in source.prefetch(urls, 3, tick=lambda: (ticks.append(1), seen.append(source.active))):
        pass
    assert ticks and max(seen) == 3  # the three were being fetched together while the first was awaited
    assert source.downloaded == 3 and source.active == 0


def test_the_titles_being_fetched_are_known_while_they_are_fetched(lib):
    import time
    seen = []
    source = Bedetheque(lib, fetch=lambda url: (time.sleep(0.8), b"<html>x</html>")[1], sleep=lambda s: None, parallel=3)
    urls = ["https://www.bedetheque.com/BD-Abaddon-Shadmi-Tome-1-Abaddon-189520.html",
            "https://www.bedetheque.com/BD-Ada-Baldi-Ada-77.html",
            "https://www.bedetheque.com/BD-1984-Derrien-Torregrossa-1984-413225.html"]
    for _ in source.prefetch(urls, 3, tick=lambda: seen.append(source.fetching_now())):
        pass
    assert seen and sorted(seen[0]) == ["1984-Derrien-Torregrossa-1984", "Abaddon-Shadmi-Tome-1-Abaddon", "Ada-Baldi-Ada"]
    assert source.fetching_now() == []  # nothing is being fetched once they are all in
