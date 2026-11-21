"""The letter index of Bédéthèque: ``bandes_dessinees_0.html``, ``_a`` ... ``_z``, one page per first letter listing every series.

A series is one line (its page address, a flag, a title). The pages carry no album: they are the cheap, complete list of series
names, fetched once by ``bdnex init``; a series page itself is read on demand only (``Bedetheque.series_by_address``).
"""

from __future__ import annotations

import html
import re
import string
from dataclasses import dataclass
from typing import Optional

from bdnex.library.query import fold

LETTERS = ("0", *string.ascii_lowercase)
LETTER_URL = "https://www.bedetheque.com/bandes_dessinees_{letter}.html"

_ITEM = re.compile(
    r'<li>\s*<a\s+href="(?P<url>https?://[^"]*?/serie-(?P<id>\d+)-BD-(?P<slug>[^"/]+?)\.html)"[^>]*>(?P<body>.*?)</a>', re.S | re.I)
_FLAG = re.compile(r'/flags/([^"/.]+)\.png', re.I)
_TITLE = re.compile(r'<span[^>]*class="bdt-liste-libelle"[^>]*>(?P<t>.*?)</span>', re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_TRAILING_ARTICLE = re.compile(r"^(?P<name>.*?)\s*\((?P<article>L'|La|Le|Les|Un|Une|The|Die|Der|Das|El|Los|Las|Il|Lo|Gli)\)\s*$", re.I)
_DISAMBIGUATION = re.compile(r"\s*\([^()]*\)\s*$")


@dataclass(frozen=True)
class SeriesEntry:
    series_id: str
    url: str
    name: str  # as listed, the trailing article put back in front: ``Nains (Les)`` -> ``Les Nains``
    country: str  # the flag: France, USA, Japan... ('' when none)
    letter: str

    @property
    def folded(self) -> str:
        return fold(self.title)

    @property
    def title(self) -> str:
        """The name without what tells two series of one name apart (``(2024)``, ``(Marvel - 2008)``): what a file name says."""
        return _DISAMBIGUATION.sub("", self.name).strip() or self.name


def letter_url(letter: str) -> str:
    return LETTER_URL.format(letter=letter.lower())


def clean_title(raw: str) -> str:
    """The listed title as text: tags and entities gone, the trailing article of the French style (``Nains (Les)``) put in front."""
    text = html.unescape(_TAGS.sub("", raw)).strip()
    text = re.sub(r"\s+", " ", text)
    found = _TRAILING_ARTICLE.match(text)
    if found:
        article = found.group("article")
        text = f"{article}{'' if article.endswith(chr(39)) else ' '}{found.group('name')}"
    return text


def parse_letter_page(content: bytes | str, letter: str = "") -> list[SeriesEntry]:
    """The series of one letter page, in the page's order. A line that is not a series link is ignored."""
    page = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    entries, seen = [], set()
    for item in _ITEM.finditer(page):
        series_id = item.group("id")
        if series_id in seen:
            continue
        seen.add(series_id)
        body = item.group("body")
        title = _TITLE.search(body)
        name = clean_title(title.group("t") if title else body) or item.group("slug").replace("-", " ")
        flag = _FLAG.search(body)
        entries.append(SeriesEntry(series_id, item.group("url"), name, flag.group(1) if flag else "", letter))
    return entries


def announced_count(content: bytes | str) -> Optional[int]:
    """The number of series the page says it has (``<small>5 514 séries</small>``), to tell a cut page from a whole one."""
    page = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    found = re.search(r"<h1>[^<]*<small>\s*([\d\s  &nbsp;]+?)\s*s[ée]ries?\s*</small>", page, re.I)
    if not found:
        return None
    digits = re.sub(r"\D", "", html.unescape(found.group(1)))
    return int(digits) if digits else None
