"""What the review of the sources found: an error page must not end the album list, a normal Cloudflare page is not a challenge,
and a series page lists the albums of its own series."""

import pytest

from bdnex.importer.bedetheque import Bedetheque, parser, sitemap
from bdnex.importer.bedetheque.bedetheque import BlockedError, looks_blocked
from bdnex.library import Library
from helpers import FakeWeb


def urlset(*addresses):
    ns = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
    return (f'<?xml version="1.0"?><urlset {ns}>' + "".join(f"<url><loc>{a}</loc></url>" for a in addresses) + "</urlset>").encode()


def album(n, slug="Serie-Tome-1-Titre"):
    return f"https://www.bedetheque.com/BD-{slug}-{n}.html"


class Site:
    """A site whose sitemap files 0-2 hold albums, the one after them is empty, and some answers can be replaced by an error page."""

    def __init__(self, broken=None):
        self.broken, self.calls = broken, []

    def __call__(self, url):
        self.calls.append(url)
        index = sitemap.sitemap_urls(10).index(url)
        if index == self.broken:
            return b"<html><head><title>502 Bad Gateway</title></head><body>cloudflare</body></html>"
        if index < 3:
            return urlset(album(index * 10 + 1, f"Serie{index}-Tome-1-Titre"), album(index * 10 + 2, f"Serie{index}-Tome-2-Titre"))
        return urlset()  # the empty one past the last file


def source(fetch):
    library = Library(":memory:")
    return Bedetheque(library, fetch=fetch, sitemap_fetch=fetch, sleep=lambda s: None), library


# ------------------------------------------------------------------------------------------------ the sitemaps
def test_an_error_page_is_not_the_end_of_the_list_it_is_refused_and_not_stored():
    site = Site(broken=1)
    src, library = source(site)
    with pytest.raises(BlockedError, match="not a sitemap"):
        src.update_sitemaps()
    urls = sitemap.sitemap_urls(10)
    assert library.cache_get("bedetheque", "sitemap", urls[0]) is not None  # what came before is kept
    assert library.cache_get("bedetheque", "sitemap", urls[1]) is None  # the error page was not stored as sitemap 1
    site.broken = None
    assert src.update_sitemaps() >= 1  # the next run goes on from there
    assert len(src.catalogue()) == 6  # all three files, none lost


def test_the_real_end_of_the_list_is_still_found_and_an_empty_urlset_is_empty():
    assert sitemap.is_empty_sitemap(b'<urlset xmlns="x"></urlset>') and sitemap.is_empty_sitemap("<urlset/>")
    assert not sitemap.is_empty_sitemap(urlset(album(1)))
    assert not sitemap.is_empty_sitemap(b"<html>Bad gateway</html>") and not sitemap.is_sitemap(b"<html>Bad gateway</html>")
    src, library = source(Site())
    src.update_sitemaps()
    assert len(src.catalogue()) == 6


def test_an_error_page_stored_by_an_earlier_version_is_fetched_again_and_the_list_says_where_it_stops(caplog):
    site = Site()
    src, library = source(site)
    urls = sitemap.sitemap_urls(10)
    library.cache_put("bedetheque", "sitemap", urls[0], urlset(album(1, "Serie0-Tome-1-Titre")))
    library.cache_put("bedetheque", "sitemap", urls[1], b"<html>502 Bad Gateway</html>")  # taken for the end of the list before
    assert len(src.catalogue()) == 1 and "not a sitemap" in caplog.text  # the list stops there, and says so
    src, _ = (Bedetheque(library, fetch=site, sitemap_fetch=site, sleep=lambda s: None), None)
    src.update_sitemaps()
    assert len(src.catalogue()) == 5  # `bdnex init` fetched sitemap 1 again, and went on (sitemap 0 as stored holds one album)


# ------------------------------------------------------------------------------------------------ the challenge marker
def test_the_script_cloudflare_adds_to_ordinary_pages_is_not_a_challenge():
    ordinary = (b"<html><head><title>Bill Baroud</title></head><body>an album</body>"
                b'<script>(function(){var a=document.createElement("script");a.src="/cdn-cgi/challenge-platform/scripts/jsd/main.js";'
                b"})()</script></html>")
    assert looks_blocked(200, ordinary) is False
    assert looks_blocked(200, b'<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>') is True  # a real one


# ------------------------------------------------------------------------------------------------ a series' own albums
SERIES_PAGE = "".join(f'<a href="{href}">x</a>' for href in [
    album(1, "Blake-Tome-1-Le-secret"), album(2, "Blake-Tome-2-Le-piege"),
    album(3, "Blake-INT-L-integrale"),
    album(4, "Blake-et-Mortimer-Tome-1-Le-secret-de-l-Espadon"), album(5, "Blake-et-Mortimer-Tome-2-Le-mystere-de-la-grande-pyramide"),
    album(6, "Blake-et-Mortimer-INT-L-integrale"),
])


def test_a_series_page_does_not_take_the_albums_of_a_longer_series_name_that_starts_with_its_own():
    albums = parser.parse_series_details(SERIES_PAGE, "Blake")["albums"]
    assert [a["source_id"] for a in albums] == ["1", "2", "3"]  # not 4, 5 and 6 (Blake et Mortimer)
    mortimer = parser.parse_series_details(SERIES_PAGE, "Blake-et-Mortimer")["albums"]
    assert [a["source_id"] for a in mortimer] == ["4", "5", "6"]


def test_a_series_page_without_any_other_series_keeps_its_specials_and_one_shots():
    page = "".join(f'<a href="{h}">x</a>' for h in [album(1, "Fell-2005-INT01-Feral-City"), album(2, "Fell-INT-Integrale")])
    assert [a["source_id"] for a in parser.parse_series_details(page, "Fell")["albums"]] == ["1", "2"]
