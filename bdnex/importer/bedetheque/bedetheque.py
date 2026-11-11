"""Bédéthèque metadata source.

All network responses are stored in the library's ``source_cache`` table, so
once an album is cached no further request is made unless ``recache`` is set.
"""

from __future__ import annotations

import logging
import time
from random import uniform
from typing import Callable, Optional

import requests
from rapidfuzz import fuzz, process

from bdnex.importer.bedetheque import parser, sitemap
from bdnex.importer.bedetheque.sitemap import Candidate
from bdnex.library import AlbumInfo, Library

SOURCE = parser.SOURCE
USER_AGENT = "bdnex (+https://github.com/lbesnard/bdnex)"

logger = logging.getLogger(__name__)


class BlockedError(RuntimeError):
    """The site answered with an anti-bot challenge instead of the page."""


def looks_blocked(status_code: int, content: bytes) -> bool:
    """Detect a Cloudflare-style challenge page (never cache one)."""
    head = content[:4000]
    return (status_code in (403, 429, 503)
            and (b"Just a moment" in head or b"challenges.cloudflare.com" in head)) \
        or (b"<title>Just a moment" in head)


def http_fetch(url: str) -> bytes:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if looks_blocked(response.status_code, response.content):
        raise BlockedError(
            f"{url} is behind an anti-bot challenge; bdnex can not fetch it automatically")
    response.raise_for_status()
    return response.content


class Bedetheque:
    """Look albums up on Bédéthèque, through the library cache.

    :param library: where responses are cached
    :param fetch: ``url -> bytes``; replaced in tests
    :param delay: ``(min, max)`` seconds slept before each *real* request
    :param sleep: replaced in tests
    """

    def __init__(self, library: Library, fetch: Callable[[str], bytes] = http_fetch,
                 delay: tuple[float, float] = (3, 10), sleep: Callable[[float], None] = time.sleep):
        self.library = library
        self._fetch = fetch
        self.delay = delay
        self._sleep = sleep
        self._index: Optional[list[Candidate]] = None

    # --------------------------------------------------------------- fetching
    def _get(self, kind: str, url: str, recache: bool = False) -> bytes:
        if not recache:
            cached = self.library.cache_get(SOURCE, kind, url)
            if cached is not None:
                return cached
        self._sleep(uniform(*self.delay))
        logger.debug(f"Fetching {url}")
        content = self._fetch(url)
        self.library.cache_put(SOURCE, kind, url, content)
        return content

    # --------------------------------------------------------------- sitemaps
    def update_sitemaps(self, recache: bool = False) -> int:
        """Download (or refresh) all sitemaps; returns how many were fetched."""
        fetched = 0
        for url in sitemap.sitemap_urls():
            if not recache and self.library.cache_get(SOURCE, "sitemap", url) is not None:
                continue
            self._get("sitemap", url, recache=True)
            fetched += 1
        self._index = None
        return fetched

    def sitemaps_cached(self) -> int:
        return sum(1 for u in sitemap.sitemap_urls()
                   if self.library.cache_get(SOURCE, "sitemap", u) is not None)

    def _load_index(self) -> list[Candidate]:
        if self._index is None:
            index: list[Candidate] = []
            for url in sitemap.sitemap_urls():
                content = self.library.cache_get(SOURCE, "sitemap", url)
                if content is not None:
                    index.extend(sitemap.parse_sitemap(content))
            if not index:
                raise FileNotFoundError("No Bédéthèque sitemaps cached: run update_sitemaps() first")
            self._index = index
        return self._index

    # --------------------------------------------------------------- matching
    def candidates(self, name: str, limit: int = 5) -> list[tuple[Candidate, float]]:
        """Best fuzzy matches for ``name`` (score 0-100). No network access."""
        index = self._load_index()
        query = sitemap.remove_common_words(name)
        hits = process.extract(query, [c.name for c in index], scorer=fuzz.ratio, limit=limit)
        return [(index[pos], score) for _, score, pos in hits]

    # ------------------------------------------------------------------ pages
    def album_info(self, url: str, recache: bool = False) -> AlbumInfo:
        """Fetch (or read from cache) an album and its series page, return its metadata."""
        html = self._get("album", url, recache).decode("utf-8", errors="replace")
        fields, series_href = parser.parse_album_page(html, url)

        abstract = None
        if series_href and fields.get("Tome") is not None:
            try:
                series_html = self._get("series", series_href, recache).decode("utf-8", errors="replace")
                abstract = parser.parse_series_page(series_html)
            except Exception as err:  # a missing series page must not lose the album
                logger.warning(f"Series page {series_href} unavailable: {err}")
        return parser.build_album_info(fields, series_href, abstract)

    def cover(self, info: AlbumInfo, recache: bool = False) -> Optional[bytes]:
        """Cover image bytes (cached)."""
        if not info.cover_url:
            return None
        return self._get("cover", info.cover_url, recache)
