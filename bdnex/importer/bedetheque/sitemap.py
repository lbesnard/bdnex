"""Bédéthèque sitemaps: download list, parsing and name normalisation.

Sitemaps only carry the album URL slug (which embeds the album id), so matching
against them is purely local and gives a ranked list of candidates.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from bdnex.importer.source import Candidate

SITEMAP_PAGE = 10000
MAX_SITEMAPS = 200  # safety cap; the real number grows with the site (55+ in 2026)
SITEMAP_URL = "https://www.bedetheque.com/albums_{lo}_{hi}_map.xml"

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
       "xhtml": "http://www.w3.org/1999/xhtml"}

STOPWORDS = frozenset(["le", "de", "a", "les", "l", "au", "int", "des", "aut", "du", "tome",
                       "un", "la", "et", "en", "que", "il", "ne", "se"])


def sitemap_url(index: int) -> str:
    """URL of the ``index``-th (0-based) sitemap file, 10000 album ids each."""
    lo = index * SITEMAP_PAGE + 1
    return SITEMAP_URL.format(lo=lo, hi=lo + SITEMAP_PAGE - 1)


def sitemap_urls(count: int = MAX_SITEMAPS) -> list[str]:
    """Sitemap file URLs, in order. The site decides how many are non-empty."""
    return [sitemap_url(i) for i in range(count)]


def remove_common_words(text: str) -> str:
    """Lowercase and drop French stopwords, which hurt fuzzy matching."""
    return " ".join(w.lower() for w in text.split() if w.lower() not in STOPWORDS)


def normalise_slug(url: str) -> str:
    """``.../BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html`` -> ``nains 1 redwin forge``."""
    slug = url.rsplit("/", 1)[-1]
    slug = re.sub(r"^BD-", "", slug)
    slug = re.sub(r"\.html$", "", slug).replace("-", " ")
    slug = re.sub(r"\d+$", "", slug.strip())  # trailing album id
    return remove_common_words(slug)


def source_id_from_url(url: str) -> str | None:
    match = re.search(r"-(\d+)\.html$", url)
    return match.group(1) if match else None


def volume_from_url(url: str) -> int | None:
    match = re.search(r"-Tome-(\d+)(?:-|\.html)", url)
    return int(match.group(1)) if match else None


def parse_sitemap(content: bytes | str) -> list[Candidate]:
    """Extract the album links (``<loc>``) of one sitemap file.

    Older sitemaps also listed ``m.bedetheque.com`` alternates; the current ones
    do not, so only ``<loc>`` is used.
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    candidates = []
    root = ET.fromstring(content)
    for loc in root.iterfind(".//sm:loc", _NS):
        href = (loc.text or "").strip()
        if "/BD-" not in href:
            continue
        source_id = source_id_from_url(href)
        if source_id is None:
            continue
        candidates.append(Candidate(source_id, href, normalise_slug(href), volume_from_url(href)))
    return candidates


def is_empty_sitemap(content: bytes | str) -> bool:
    """True for the empty ``<urlset/>`` the site serves past its last file."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return b"<loc>" not in content
