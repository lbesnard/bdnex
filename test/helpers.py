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


def _a_cover() -> bytes:
    """A small real picture (a cover is checked to be one before it is kept)."""
    import pymupdf

    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 240, 360), False)
    pixmap.set_rect(pixmap.irect, (200, 120, 60))
    return pixmap.tobytes("png")


COVER_BYTES = _a_cover()


class FakeWeb:
    """Replaces the network: serves fixtures and counts requests."""

    def __init__(self):
        self.calls = []
        self.pages = {ALBUM_URL: _read(os.path.join(_TEST_ROOT, 'mobile_redwin.html')), SERIES_URL: _read(os.path.join(_BDGEST, 'series_html/serie-47467-BD-Nains.html')),
                      "https://www.bedetheque.com/media/Couvertures/Couv_245127.jpg": COVER_BYTES}
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




# ------------------------------------------------------- a non-Bédéthèque source
from rapidfuzz import fuzz  # noqa: E402

from bdnex.importer.source import Candidate  # noqa: E402
from bdnex.library.models import AlbumInfo, SeriesInfo  # noqa: E402

FAKE_CATALOG = [
    Candidate("100", "https://fake.example/albums/100", "tintin lotus bleu", 5),
    Candidate("101", "https://fake.example/albums/101", "tintin crabe pinces or", 9),
    Candidate("200", "https://fake.example/albums/200", "maus", None),
]


class FakeSource:
    """A complete MetadataSource that has nothing to do with Bédéthèque."""

    name = "fake"
    fetch_errors = (KeyError,)

    def __init__(self, library, options):
        self.library = library
        self.options = options
        self.prepared = 0
        self.fetched = []

    def is_ready(self):
        return self.prepared > 0

    def prepare(self, recache=False, progress=None):
        self.prepared += 1
        if progress:
            progress("  fake index loaded")

    def candidates(self, query, limit=5):
        scored = [(c, float(fuzz.ratio(query.lower(), c.name))) for c in FAKE_CATALOG]
        return sorted(scored, key=lambda x: x[1], reverse=True)[:limit]

    def album_info(self, url, recache=False):
        self.fetched.append(url)
        if url in self.options.get("unavailable", []):
            raise KeyError(url)
        if url.endswith("/200"):
            return AlbumInfo("fake", "200", "Maus", credits={"Writer": ["Spiegelman, Art"]}, language="en")
        number = {"100": 5, "101": 9}[url.rsplit("/", 1)[-1]]
        return AlbumInfo("fake", url.rsplit("/", 1)[-1], f"Tintin {number}",
                         series=SeriesInfo("fake", "tintin", "Tintin"), volume=number,
                         credits={"Writer": ["Hergé"]}, language="fr")

    def partial_info(self, candidate):
        return AlbumInfo("fake", candidate.source_id, candidate.name, extra={"partial": True})


def create_fake_source(library, settings):
    return FakeSource(library, settings.plugins.get("fake", {}))


def create_incomplete_source(library, settings):
    return object()


FETCHED = []


def recording_fetch(url):
    """A configured fetcher that would reach the network: tests assert it is never called."""
    FETCHED.append(url)
    return b"<html>should not be used</html>"
