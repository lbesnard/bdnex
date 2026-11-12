"""Bédéthèque sitemaps: download list, parsing and name normalisation.

Sitemaps only carry the album URL slug (which embeds the album id), so matching
against them is purely local and gives a ranked list of candidates.
"""

from __future__ import annotations

import re
import unicodedata
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


# Words in file names that say nothing about the album (language, format, scan group, ...).
NOISE_WORDS = frozenset(["fr", "en", "vf", "vo", "eng", "fra", "digital", "ebook", "officiel", "comics",
                         "hd", "cbz", "cbr", "pdf", "alb", "tpb", "scan", "scans", "bd", "num",
                         "numerique", "zip", "rar", "complet", "tomes"])
_VOLUME_WORDS = frozenset(["t", "v", "tome", "vol", "volume", "no", "n", "#"])


def fold(text: str) -> str:
    """Lowercase and strip accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def query_tokens(text: str) -> tuple[list[str], list[int]]:
    """Split a file-name query into (title words, volume numbers).

    ``"Bleach T68 (Tite KUBO) FR"`` -> ``(["bleach", ...], [68])``. Accents, apostrophes, stopwords
    and noise words (``Fr``, ``ALB``, ``eBook``...) are dropped; ``T01``/``Tome 1``/``v3``/``#7`` are
    volumes. Without such a marker, a bare number of up to three digits counts as the volume.
    """
    text = re.sub(r"\\\d{3}", "", text)  # escapes left by badly decoded archive names
    raw = re.sub(r"[^a-z0-9#]+", " ", fold(text).replace("'", " ").replace("\u2019", " ")).split()
    words: list[str] = []
    volumes: list[int] = []
    bare: list[int] = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        marked = re.fullmatch(r"(?:t|v|tome|vol|volume|no|n|#)0*(\d{1,3})", tok)
        if marked:
            volumes.append(int(marked.group(1)))
        elif tok in _VOLUME_WORDS and i + 1 < len(raw) and re.fullmatch(r"\d{1,3}", raw[i + 1]):
            volumes.append(int(raw[i + 1]))
            i += 1
        elif re.fullmatch(r"\d{1,3}", tok):
            bare.append(int(tok))
        elif tok not in STOPWORDS and tok not in NOISE_WORDS and tok not in _VOLUME_WORDS and tok not in words:
            words.append(tok)
        i += 1
    return words, volumes or bare
