"""Bédéthèque sitemaps: download list, parsing and name normalisation.

Sitemaps only carry the album URL slug (which embeds the album id), so matching
against them is purely local and gives a ranked list of candidates.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

SITEMAP_COUNT = 47
SITEMAP_PAGE = 10000
SITEMAP_URL = "https://www.bedetheque.com/albums_{lo}_{hi}_map.xml"

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
       "xhtml": "http://www.w3.org/1999/xhtml"}

STOPWORDS = frozenset(["le", "de", "a", "les", "l", "au", "int", "des", "aut", "du", "tome",
                       "un", "la", "et", "en", "que", "il", "ne", "se"])


@dataclass(frozen=True)
class Candidate:
    source_id: str
    url: str  # mobile url
    name: str  # normalised name used for matching


def sitemap_urls() -> list[str]:
    """The 47 sitemap files, 10000 albums each."""
    urls, last = [], 0
    for _ in range(SITEMAP_COUNT):
        lo = last + 1
        hi = lo + SITEMAP_PAGE - 1
        last = hi
        urls.append(SITEMAP_URL.format(lo=lo, hi=hi))
    return urls


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


def parse_sitemap(content: bytes | str) -> list[Candidate]:
    """Extract the mobile album links of one sitemap file."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    candidates = []
    root = ET.fromstring(content)
    for link in root.iterfind(".//xhtml:link", _NS):
        href = link.get("href", "")
        if "m.bedetheque.com/BD-" not in href:
            continue
        source_id = source_id_from_url(href)
        if source_id is None:
            continue
        candidates.append(Candidate(source_id, href, normalise_slug(href)))
    return candidates
