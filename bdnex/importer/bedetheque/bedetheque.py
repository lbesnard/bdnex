"""Bédéthèque metadata source.

All network responses are stored in the library's ``source_cache`` table, so
once an album is cached no further request is made unless ``recache`` is set.
"""

from __future__ import annotations

import logging
import threading
import re
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


CHALLENGE_MARKERS = (b"<title>Just a moment", b"window._cf_chl_opt", b"/cdn-cgi/challenge-platform/")


def looks_blocked(status_code: int, content: bytes) -> bool:
    """Detect a Cloudflare-style challenge page (never cache one).

    The page itself is recognised whatever the status code, because a fetcher that solved or timed out
    may hand back the challenge with a 200.
    """
    head = content[:20000]
    return (status_code in (403, 429, 503)
            and (b"Just a moment" in head or b"challenges.cloudflare.com" in head)) \
        or any(marker in head for marker in CHALLENGE_MARKERS)


def http_fetch(url: str) -> bytes:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if looks_blocked(response.status_code, response.content):
        raise BlockedError(
            f"{url} is behind an anti-bot challenge; bdnex can not fetch it automatically")
    response.raise_for_status()
    return response.content


def image_fetch(url: str) -> bytes:
    """A cover: a plain request (the page fetcher of the configuration is made for HTML pages: a browser-based one
    hands back the little page that wraps an image, not the image)."""
    response = requests.get(url, headers={"User-Agent": USER_AGENT, "Referer": "https://www.bedetheque.com/"},
                            timeout=30)
    response.raise_for_status()
    return response.content


NUMERIC_TITLE_SCORE = 82.0  # an album titled with exactly the numbers of the file name
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
                 sitemap_delay: tuple[float, float] = (0.5, 1.5),
                 image_fetch: Optional[Callable[[str], bytes]] = None, parallel: int = 1):
        self.library = library
        self._fetch = fetch
        self._image_fetch = image_fetch or fetch
        self.parallel = parallel  # album pages fetched at once (see `prefetch`)
        self.active = 0       # pages being fetched right now (waiting the polite delay included)
        self._fetching: list[str] = []  # their addresses, in the order they started
        self.downloaded = 0   # pages fetched since `prefetch` started
        self._stats = threading.Lock()
        self._sitemap_fetch = sitemap_fetch or fetch
        self.delay = delay
        self.sitemap_delay = sitemap_delay
        self._sleep = sleep
        self._index: Optional[list[Candidate]] = None
        self._words: Optional[dict[str, list[int]]] = None

    # --------------------------------------------------------------- fetching
    def _get(self, kind: str, url: str, recache: bool = False, store: bool = True) -> bytes:
        if not recache:
            cached = self.library.cache_get(SOURCE, kind, url)
            if cached is not None and kind != "sitemap" and looks_blocked(200, cached):
                logger.warning(f"The stored {kind} for {url} is an anti-bot challenge page: dropped")
                self.library.cache_delete(SOURCE, kind, url)
                cached = None
            if cached is not None:
                return cached
        content = self._download(kind, url)
        if store:
            self.library.cache_put(SOURCE, kind, url, content)
        return content

    def _download(self, kind: str, url: str) -> bytes:
        """One real request, after the polite wait. Touches no database: it can run in a worker thread."""
        self._sleep(uniform(*(self.sitemap_delay if kind == "sitemap" else self.delay)))
        logger.debug(f"Fetching {url}")
        content = {"sitemap": self._sitemap_fetch, "cover": self._image_fetch}.get(kind, self._fetch)(url)
        if kind not in ("sitemap", "cover") and looks_blocked(200, content):  # whichever fetcher it came from
            raise BlockedError(f"{url}: the fetcher returned an anti-bot challenge page, not the page. "
                               "Nothing was stored.")
        return content

    def _tracked_download(self, url: str) -> bytes:
        with self._stats:
            self.active += 1
            self._fetching.append(url)
        try:
            return self._download("album", url)
        finally:
            with self._stats:
                self.active -= 1
                self.downloaded += 1
                self._fetching.remove(url)

    def fetching_now(self) -> list[str]:
        """The short names of the pages being fetched right now (``Abaddon-Shadmi-Tome-1-Abaddon``), oldest first."""
        with self._stats:
            urls = list(self._fetching)
        return [re.sub(r"-\d+$", "", re.sub(r"\.html$", "", re.sub(r"^BD-", "", url.rsplit("/", 1)[-1]))) for url in urls]

    def prefetch(self, urls: list[str], workers: Optional[int] = None, recache: bool = False, tick=None):
        """Download album pages ``workers`` at a time. Yields ``(url, error or None)`` in the order of ``urls``; each page
        is stored in the library by the calling thread as it is yielded, so the caller then finds it in the cache.

        Every request waits ``delay`` first, in its own thread: several workers fetch faster, not more politely.
        ``tick()`` is called about twice a second while the next page is awaited, so that a progress display can show
        what is going on (``active`` and ``downloaded`` count the pages being fetched and fetched so far).
        """
        from collections import deque
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as Waiting

        workers = max(1, workers or self.parallel)
        self.active = self.downloaded = 0
        self._fetching = []
        todo = [u for u in urls if recache or self.library.cache_get(SOURCE, "album", u) is None]
        wanted = set(todo)
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bdnex-page")
        waiting: deque = deque()
        queue = iter(todo)
        window = workers * 3  # how many are submitted ahead: bounds the pages held in memory

        def top_up():
            while len(waiting) < window:
                url = next(queue, None)
                if url is None:
                    return
                waiting.append((url, executor.submit(self._tracked_download, url)))

        try:
            top_up()
            results: dict[str, Optional[Exception]] = {}
            for url in urls:
                if url not in wanted:
                    yield url, None  # already in the library
                    continue
                while url not in results:  # the pages come back in the order they were asked for
                    head, future = waiting.popleft()
                    top_up()
                    try:
                        while True:
                            try:
                                content = future.result(timeout=0.5)
                                break
                            except Waiting:
                                if tick:
                                    tick()
                        self.library.cache_put(SOURCE, "album", head, content)
                        results[head] = None
                    except Exception as err:  # this page failed; the others go on
                        results[head] = err
                yield url, results.pop(url)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

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
    def catalogue(self) -> list[Candidate]:
        """Every album of the cached album list (for searching it by hand). No network access."""
        return self._load_index()

    ALBUM_PAGE = re.compile(r"^https?://(?:www\.|m\.)?bedetheque\.com/BD-[^/\s]+-\d+\.html$", re.I)

    def candidate_from_url(self, url: str) -> Optional[Candidate]:
        """The album a typed-in page address stands for (``None`` if it is not an album page of the site)."""
        url = (url or "").strip().split("#")[0].split("?")[0]
        if not self.ALBUM_PAGE.match(url):
            return None
        url = re.sub(r"^https?://(?:www\.|m\.)?", "https://www.", url, flags=re.I)
        source_id = sitemap.source_id_from_url(url)
        if source_id is None:
            return None
        return Candidate(source_id, url, sitemap.normalise_slug(url), sitemap.volume_from_url(url))

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

    def _numeric_titles(self) -> dict[tuple[str, ...], list[int]]:
        """Albums whose title is made only of small numbers (``14-18``, ``666``): numbers -> positions in the index.

        Such a title has no word to find it by, and small numbers are not indexed (they are volumes). The title is what
        stands before ``-Tome-N`` in the page address (or all of it, for an album with no volume).
        """
        if getattr(self, "_numeric", None) is None:
            numeric: dict[tuple[str, ...], list[int]] = {}
            for pos, cand in enumerate(self._load_index()):
                slug = re.sub(r"\.html$", "", cand.url.rsplit("/", 1)[-1])
                slug = re.sub(r"^BD-", "", slug)
                head = re.split(r"-Tome-\d", slug, maxsplit=1)[0] if "-Tome-" in slug else re.sub(r"-\d+$", "", slug)
                tokens = head.split("-")
                if tokens and all(t.isdigit() and len(t) <= 3 for t in tokens):
                    numeric.setdefault(tuple(t.lstrip("0") or "0" for t in tokens), []).append(pos)
            self._numeric = numeric
        return self._numeric

    def numeric_title_candidates(self, numbers: list[str], limit: int = 20) -> list[tuple[Candidate, float]]:
        """The albums titled with exactly these numbers (``["14", "18"]``: the series ``14-18``), a good score each: the
        title is a perfect match, the volume is for the caller to check."""
        index = self._load_index()
        key = tuple(n.lstrip("0") or "0" for n in numbers)
        found = [index[pos] for pos in self._numeric_titles().get(key, [])]
        found.sort(key=lambda c: (c.volume is None, c.volume or 0))
        return [(cand, NUMERIC_TITLE_SCORE) for cand in found[:limit]]

    # ------------------------------------------------------------------ pages
    def album_info(self, url: str, recache: bool = False) -> AlbumInfo:
        """Fetch (or read from cache) an album and its series page, return its metadata."""
        html = self._get("album", url, recache).decode("utf-8", errors="replace")
        fields, series_href = parser.parse_album_page(html, url)

        abstract, series_extra = None, {}
        if series_href and fields.get("Tome") is not None:
            try:
                series_html = self._get("series", series_href, recache).decode("utf-8", errors="replace")
                details = parser.parse_series_details(series_html)
                abstract = details.pop("summary", None)
                series_extra = details  # the albums the series page lists
            except Exception as err:  # a missing series page must not lose the album
                logger.warning(f"Series page {series_href} unavailable: {err}")
        return parser.build_album_info(fields, series_href, abstract, series_extra)

    def album_info_from_html(self, html: str, url: str) -> AlbumInfo:
        """Use a page saved from a browser (the site blocks scripted page requests).

        The page is stored in the cache, so later ``album_info(url)`` calls work
        offline.
        """
        if looks_blocked(200, html.encode("utf-8", errors="replace")):
            raise BlockedError(f"{url}: the saved page is an anti-bot challenge page, not the album page")
        self.library.cache_put(SOURCE, "album", url, html)
        return self.album_info(url)

    def cover_image(self, url: str, recache: bool = False) -> Optional[bytes]:
        """The image at ``url`` (an album's ``cover_url``): one polite request. It is not kept in the library
        cache: the importer saves it as a file (``cover.dir``)."""
        return self._get("cover", url, recache, store=False) if url else None
