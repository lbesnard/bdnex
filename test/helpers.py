"""Test helpers importable by dotted path (used for the `fetcher:` config option)."""


def fake_fetch(url):
    """A plain `url -> bytes` function, as named by `bedetheque.fetcher`."""
    return f"<html>{url}</html>".encode()


# ------------------------------------------------------------------ fake web
import os  # noqa: E402

from bdnex.importer.bedetheque import sitemap  # noqa: E402

_TEST_ROOT = os.path.dirname(__file__)
_BDGEST = os.path.join(_TEST_ROOT, ".local/share/bdnex/bedetheque")
ALBUM_URL = "https://m.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html"
SERIES_URL = "https://m.bedetheque.com/serie-47467-BD-Nains.html"


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


class FakeWeb:
    """Replaces the network: serves fixtures and counts requests."""

    def __init__(self):
        self.calls = []
        self.pages = {ALBUM_URL: _read(os.path.join(_TEST_ROOT, 'mobile_redwin.html')), SERIES_URL: _read(os.path.join(_BDGEST, 'series_html/serie-47467-BD-Nains.html')),
                      "https://www.bedetheque.com/media/Couvertures/Couv_245127.jpg": b"JPEGDATA"}
        ns = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        for n, url in enumerate(sitemap.sitemap_urls(60)):
            if n < 26:  # 26 non-empty files, then the site serves empty ones
                self.pages[url] = (f'<?xml version="1.0"?><urlset {ns}><url><loc>https://www.bedetheque.com/'
                                   f'BD-Dummy-{n}-{n + 1000000}.html</loc></url></urlset>').encode()
            else:
                self.pages[url] = f'<?xml version="1.0"?><urlset {ns}>\n</urlset>'.encode()
        for name in os.listdir(os.path.join(_BDGEST, 'sitemaps')):  # real fixtures replace files 25 and 26
            self.pages["https://www.bedetheque.com/" + name] = _read(os.path.join(os.path.join(_BDGEST, 'sitemaps'), name))

    def __call__(self, url):
        self.calls.append(url)
        return self.pages[url]


