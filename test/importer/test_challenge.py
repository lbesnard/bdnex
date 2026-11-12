"""A Cloudflare challenge page is never stored or parsed as an album or series page, whoever fetched it."""

import pytest

from bdnex.importer.bedetheque import Bedetheque
from bdnex.importer.bedetheque.bedetheque import BlockedError, looks_blocked
from bdnex.library import Library
from helpers import ALBUM_URL, SERIES_URL, FakeWeb

# the interstitial page Cloudflare serves while it decides whether the client is a person (shortened)
CHALLENGE = (b'<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
             b'<meta name="robots" content="noindex,nofollow"></head><body><div class="main-wrapper" role="main">'
             b'</div><script nonce="x">(function(){window._cf_chl_opt = {cType: \'managed\', cZone: \'www.bedetheque.com\'};'
             b'var a = document.createElement(\'script\');a.src = \'/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1\';'
             b'}());</script></body></html>')


@pytest.mark.parametrize("status, content, blocked", [
    (200, CHALLENGE, True),  # a fetcher that failed to solve it still answers 200
    (403, b"<html>Just a moment... challenges.cloudflare.com</html>", True),
    (503, b"<html>Just a moment</html>", True),
    (200, b"<html><body>window._cf_chl_opt = {};</body></html>", True),
    (200, b"<html><body>Redwin, fils d'Ulrog</body></html>", False),
    (403, b"<html>Forbidden</html>", False),  # a plain 403 is the caller's business
])
def test_looks_blocked(status, content, blocked):
    assert looks_blocked(status, content) is blocked


def source(fetch):
    lib = Library(":memory:")
    return Bedetheque(lib, fetch=fetch, sleep=lambda s: None), lib


def test_a_challenge_from_any_fetcher_is_refused_and_not_stored():
    src, lib = source(lambda url: CHALLENGE)
    with pytest.raises(BlockedError, match="Nothing was stored"):
        src.album_info(ALBUM_URL)
    assert lib.cache_get("bedetheque", "album", ALBUM_URL) is None  # so the next run asks again


def test_a_challenge_for_the_series_page_does_not_lose_the_album():
    web = FakeWeb()
    web.pages[SERIES_URL] = CHALLENGE
    src, lib = source(web)
    info = src.album_info(ALBUM_URL)  # the album page is fine
    assert info.title and info.series.summary is None  # the series page was refused, the album is kept
    assert lib.cache_get("bedetheque", "series", SERIES_URL) is None and lib.cache_get("bedetheque", "album", ALBUM_URL)


def test_a_challenge_stored_by_an_earlier_version_is_dropped_and_fetched_again():
    web = FakeWeb()
    src, lib = source(web)
    lib.cache_put("bedetheque", "album", ALBUM_URL, CHALLENGE)  # what used to happen
    info = src.album_info(ALBUM_URL)
    assert info.title == "Redwin de la Forge" and ALBUM_URL in web.calls  # fetched again
    assert b"Just a moment" not in lib.cache_get("bedetheque", "album", ALBUM_URL)


def test_a_saved_page_that_is_the_challenge_is_refused():
    src, lib = source(FakeWeb())
    with pytest.raises(BlockedError, match="saved page"):
        src.album_info_from_html(CHALLENGE.decode(), ALBUM_URL)
    assert lib.cache_get("bedetheque", "album", ALBUM_URL) is None


def test_sitemaps_are_not_checked_for_challenges():
    # they are XML, and a huge one must not be scanned or refused because of a marker-like word
    src, lib = source(lambda url: b"<urlset><url><loc>https://x/BD-Just-a-moment-1.html</loc></url></urlset>")
    assert src._get("sitemap", "https://www.bedetheque.com/albums_1_10000_map.xml")
