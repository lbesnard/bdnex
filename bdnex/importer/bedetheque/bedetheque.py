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


VOLUME_TIEBREAK = 10.0  # only orders the candidates; the reported score does not include it
MAX_POSTINGS = 4000  # a word in more albums than this ('integrale') is no use to find candidates


class Bedetheque:
    """Look albums up on Bédéthèque, through the library cache.

    :param library: where responses are cached
    :param fetch: ``url -> bytes`` for album/series pages and covers; replaced in tests
    :param sitemap_fetch: ``url -> bytes`` for the sitemap files (defaults to ``fetch``)
    :param delay: ``(min, max)`` seconds slept before each *real* request
    :param sleep: replaced in tests
    """

    name = SOURCE
    fetch_errors = (BlockedError, FileNotFoundError, OSError)

    def __init__(self, library: Library, fetch: Callable[[str], bytes] = http_fetch,
                 delay: tuple[float, float] = (3, 10), sleep: Callable[[float], None] = time.sleep,
                 sitemap_fetch: Optional[Callable[[str], bytes]] = None,
                 sitemap_delay: tuple[float, float] = (0.5, 1.5)):
        self.library = library
        self._fetch = fetch
        self._sitemap_fetch = sitemap_fetch or fetch
        self.delay = delay
        self.sitemap_delay = sitemap_delay
        self._sleep = sleep
        self._index: Optional[list[Candidate]] = None
        self._words: Optional[dict[str, list[int]]] = None

    # --------------------------------------------------------------- fetching
    def _get(self, kind: str, url: str, recache: bool = False) -> bytes:
        if not recache:
            cached = self.library.cache_get(SOURCE, kind, url)
            if cached is not None:
                return cached
        self._sleep(uniform(*(self.sitemap_delay if kind == "sitemap" else self.delay)))
        logger.debug(f"Fetching {url}")
        content = (self._sitemap_fetch if kind == "sitemap" else self._fetch)(url)
        self.library.cache_put(SOURCE, kind, url, content)
        return content

    # --------------------------------------------------------------- sitemaps
    def update_sitemaps(self, recache: bool = False,
                        progress: Optional[Callable[[str], None]] = None) -> int:
        """Download (or refresh) sitemaps until the site serves an empty one.

        ``progress`` receives a short message per file fetched. Returns how many files were fetched.
        """
        fetched = 0
        for url in sitemap.sitemap_urls():
            cached = None if recache else self.library.cache_get(SOURCE, "sitemap", url)
            if cached is not None:
                if sitemap.is_empty_sitemap(cached):
                    break
                continue
            if progress:
                progress(f"  sitemap {fetched + 1}: {url.rsplit('/', 1)[-1]}")
            content = self._get("sitemap", url, recache=True)
            fetched += 1
            if sitemap.is_empty_sitemap(content):
                break
        self._index = None
        self._words = None
        return fetched

    # -- MetadataSource protocol
    def is_ready(self) -> bool:
        return self.sitemaps_cached() > 0

    def prepare(self, recache: bool = False, progress: Optional[Callable[[str], None]] = None) -> None:
        self.update_sitemaps(recache=recache, progress=progress)

    def partial_info(self, candidate: Candidate) -> AlbumInfo:
        return parser.partial_album_info(candidate)

    def sitemaps_cached(self) -> int:
        """Number of cached, non-empty sitemap files."""
        count = 0
        for url in sitemap.sitemap_urls():
            content = self.library.cache_get(SOURCE, "sitemap", url)
            if content is None or sitemap.is_empty_sitemap(content):
                break
            count += 1
        return count

    def _load_index(self) -> list[Candidate]:
        if self._index is None:
            index: list[Candidate] = []
            for url in sitemap.sitemap_urls():
                content = self.library.cache_get(SOURCE, "sitemap", url)
                if content is None or sitemap.is_empty_sitemap(content):
                    break
                index.extend(sitemap.parse_sitemap(content))
            if not index:
                raise FileNotFoundError("No Bédéthèque sitemaps cached: run update_sitemaps() first")
            self._index = index
        return self._index

    # --------------------------------------------------------------- matching
    def album_count(self) -> int:
        """Albums in the cached sitemaps (0 when there are none)."""
        try:
            return len(self._load_index())
        except FileNotFoundError:
            return 0

    def warm(self) -> None:
        """Load the matching index now, so worker processes forked later share it."""
        self._word_index()

    def _word_index(self) -> dict[str, list[int]]:
        """word -> positions in the index of the candidates containing it (built once)."""
        if self._words is None:
            words: dict[str, list[int]] = {}
            for pos, cand in enumerate(self._load_index()):
                for word in set(cand.name.split()):
                    if not (word.isdigit() and len(word) <= 3):
                        words.setdefault(word, []).append(pos)
            self._words = words
        return self._words

    def _pool(self, words: list[str]) -> set[int]:
        """Positions of the candidates sharing a (possibly misspelt) word with the query."""
        index = self._word_index()
        pool: set[int] = set()
        for word in words:
            postings = index.get(word)
            if postings is None and len(word) > 3:
                near = process.extract(word, list(index), scorer=fuzz.ratio, limit=3, score_cutoff=85)
                postings = [pos for other, _, _ in near for pos in index[other]]
            if postings and len(postings) <= MAX_POSTINGS:  # too common a word narrows nothing
                pool.update(postings)
        return pool

    @staticmethod
    def _score(words: list[str], cand: Candidate) -> float:
        """0-100: how many query words the candidate has, and how close the texts are.

        The volume is not part of this: the importer adjusts for it (``matching.rerank``).
        """
        cand_words = [w for w in cand.name.split() if not (w.isdigit() and len(w) <= 3)]  # volumes out, years in
        known = set(cand_words)
        hits = sum(1 for w in words if w in known
                   or (len(w) > 3 and any(fuzz.ratio(w, c) >= 85 for c in cand_words)))
        coverage = hits / len(words)
        extra = sum(1 for c in cand_words if c not in words and not c.isdigit())  # years do not count
        closeness = fuzz.ratio(" ".join(words), " ".join(cand_words))
        score = 60 * coverage + 0.4 * closeness - min(extra, 6) * 1.5 * coverage
        # every word of the album name is in the file name: the rest is probably author names
        title = {c for c in cand_words if not c.isdigit()}
        distinctive = len(title) >= 3 or (len(title) == 2 and sum(map(len, title)) >= 12)
        if distinctive and all(c in words for c in title):
            unexplained = sum(1 for w in words if w not in known)
            score = max(score, 85 - 2 * min(unexplained, 5))
        return max(0.0, min(100.0, score))

    def candidates(self, name: str, limit: int = 5) -> list[tuple[Candidate, float]]:
        """Best matches for ``name`` (score 0-100). No network access.

        Candidates are looked up by word, then scored on word coverage and text similarity, so
        ``Bleach 68`` finds ``bleach 68 the ordinary peace`` and ignores ``peach 6``.
        """
        index = self._load_index()
        words, volumes = sitemap.query_tokens(name)
        if words:
            pool = self._pool(words)
            if pool:
                # a series has many albums with the same title score: keep the wanted volume in the cut
                scored = sorted(((self._score(words, index[pos]), pos) for pos in pool),
                                key=lambda sp: sp[0] + (VOLUME_TIEBREAK if index[sp[1]].volume in volumes else 0),
                                reverse=True)
                return [(index[pos], score) for score, pos in scored[:limit]]
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

    def album_info_from_html(self, html: str, url: str) -> AlbumInfo:
        """Use a page saved from a browser (the site blocks scripted page requests).

        The page is stored in the cache, so later ``album_info(url)`` calls work
        offline.
        """
        self.library.cache_put(SOURCE, "album", url, html)
        return self.album_info(url)

    def cover(self, info: AlbumInfo, recache: bool = False) -> Optional[bytes]:
        """Cover image bytes (cached)."""
        if not info.cover_url:
            return None
        return self.cover_image(info.cover_url, recache)

    def cover_image(self, url: str, recache: bool = False) -> Optional[bytes]:
        """The image at ``url`` (an album's ``cover_url``), through the library cache: one polite request."""
        return self._get("cover", url, recache) if url else None
