"""A second place to take an album from: a series page of bdtheque.com (``https://www.bdtheque.com/series/14922/le-pere-des-etoiles``).

Not a matching source: bdnex has no list of its pages, so nothing here searches. It is for the file Bédéthèque does not know: the
person gives the address of the page (the review screen's ``i``, ``bdnex import --album ADDRESS FILE``), the page is fetched with the
same fetcher as Bédéthèque's (``fetch``, its endpoints included), kept compressed in its own table (``bdtheque_pages``),
and read into an ordinary library album with ``source = bdtheque``.

The page is a *series* page: its album list is loaded by the site's JavaScript and is not in the HTML. So an album here carries the
series' own data (title, summary, credits, publisher, genre, date, cover); the volume of a series of several tomes is the one in the
file's name.
"""

from __future__ import annotations

import html as htmllib
import json
import logging
import re
from typing import Callable, Optional

from bs4 import BeautifulSoup

from bdnex.library.models import AlbumInfo, SeriesInfo

SOURCE = "bdtheque"
logger = logging.getLogger(__name__)

ADDRESS = re.compile(r"^https?://(?:www\.)?bdtheque\.com/series/(?P<id>\d+)(?:/(?P<slug>[^/?#\s]*))?", re.I)

# the labels of the credits table -> ComicInfo roles
ROLES = {"Scénario": "Writer", "Dessin": "Penciller", "Couleurs": "Colorist", "Encrage": "Inker", "Lettrage": "Letterer",
         "Couverture": "CoverArtist", "Traduction": "Translator"}
MONTHS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
          "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12}


def is_address(text: str) -> bool:
    return ADDRESS.match((text or "").strip().split("#")[0]) is not None


def canonical(text: str) -> Optional[str]:
    """``https://www.bdtheque.com/series/14922/slug`` for any spelling of the address (http, no ``www``, a query, an anchor)."""
    found = ADDRESS.match((text or "").strip().split("#")[0].split("?")[0])
    if found is None:
        return None
    return f"https://www.bdtheque.com/series/{found['id']}" + (f"/{found['slug']}" if found["slug"] else "")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(text or "")).strip()


def _date(text: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """``22 Mai 2015`` -> (2015, 5, 22); ``Mai 2015`` -> (2015, 5, None); ``2015`` -> (2015, None, None)."""
    found = re.search(r"(?:(\d{1,2})\s+)?(?:([^\W\d_]+)\s+)?(\d{4})\b", _clean(text))
    if not found:
        return None, None, None
    return int(found.group(3)), MONTHS.get((found.group(2) or "").lower()), int(found.group(1)) if found.group(1) and found.group(2) else None


def parse_series_page(page: str, address: str = "") -> dict:
    """What a series page says, as a plain dict (``id``, ``url``, ``title``, ``summary``, ``credits``, ``publisher``, ``genres``, ``year``,
    ``month``, ``day``, ``cover_url``, ``rating``, ``tomes`` and ``themes``, ``public``, ``type``, ``story_status`` in ``extra``).
    Raises ``ValueError`` when it is not a series page."""
    soup = BeautifulSoup(page, "html.parser")
    canon = soup.find("link", rel="canonical")
    url = canonical((canon.get("href") if canon else "") or address)
    found = ADDRESS.match(url or "")
    title = soup.find("h1")
    if found is None or title is None or not _clean(title.get_text()):
        raise ValueError("not a series page of bdtheque.com (no title or no series address)")
    result: dict = {"id": found["id"], "url": url, "title": _clean(title.get_text()), "credits": {}, "genres": [], "extra": {}}
    story = soup.find(id="storyParagraph")
    meta = soup.find("meta", attrs={"property": "og:description"})
    result["summary"] = (story.get_text().strip() if story else (meta.get("content", "").strip() if meta else "")) or None
    cover = soup.find("meta", attrs={"property": "og:image"})
    result["cover_url"] = cover.get("content") if cover else None
    result["themes"] = [_clean(a.get_text()) for a in soup.select("a.badge[href*='theme=']")]
    for row in soup.select("table.table tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 2:
            continue
        label, value = _clean(cells[0].get_text(" ")), cells[1]
        links = [_clean(a.get_text()) for a in value.find_all("a")]
        if label in ROLES:
            names = result["credits"].setdefault(ROLES[label], [])
            names += [name for name in links if name and name not in names]
        elif label.lower().startswith("editeur") or label.lower().startswith("éditeur"):
            result["publisher"] = links[0] if links else None
        elif label.startswith("Genre"):
            for a in value.find_all("a"):
                href = a.get("href", "")
                text = _clean(a.get_text())
                if "genre=" in href:
                    result["genres"].append(text)
                elif "public=" in href:
                    result["extra"]["public"] = text
                elif "type=" in href:
                    result["extra"]["type"] = text
        elif label.startswith("Date de parution"):
            result["year"], result["month"], result["day"] = _date(value.get_text(" "))
        elif label.startswith("Statut"):
            if links:
                result["extra"]["story_status"] = links[0]
            tomes = re.search(r"(\d+)\s+tomes?\s+paru", _clean(value.get_text(" ")), re.I)
            if tomes:
                result["tomes"] = int(tomes.group(1))
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        rating = (data.get("aggregateRating") or {}).get("ratingValue") if isinstance(data, dict) else None
        if rating:
            try:
                result["rating"] = float(rating)
            except ValueError:
                pass
    return result


def album_info(parsed: dict, volume: Optional[int] = None) -> AlbumInfo:
    """The library album of a parsed series page. A series of one tome is a one-shot; for several tomes ``volume`` (from the file's
    name) makes the album that volume of the series: the page has the series' data, not each tome's."""
    name = parsed["title"]
    tomes = parsed.get("tomes")
    several = tomes > 1 if tomes is not None else volume is not None  # "N tomes parus" says it; without it, a volume in the file name does
    series = SeriesInfo(SOURCE, parsed["id"], name, summary=parsed.get("summary"), url=parsed["url"]) if several else None
    keep = volume if several else None
    return AlbumInfo(
        source=SOURCE, source_id=parsed["id"] + (f"-{keep}" if keep is not None else ""),
        title=f"{name} - T{keep:02d}" if keep is not None else name,
        series=series, volume=keep, publisher=parsed.get("publisher"), year=parsed.get("year"), month=parsed.get("month"),
        day=parsed.get("day"), language="fr", summary=parsed.get("summary"), rating=parsed.get("rating"),
        cover_url=parsed.get("cover_url"), url=parsed["url"], credits=dict(parsed["credits"]), genres=list(parsed["genres"]),
        extra={**parsed["extra"], "themes": parsed["themes"], "tomes": parsed.get("tomes")})


class Bdtheque:
    """The pages of bdtheque.com, fetched on demand and kept (compressed) in the library's own table."""

    name = SOURCE

    def __init__(self, library, fetch: Callable[[str], bytes]):
        self.library, self._fetch = library, fetch

    def page(self, address: str, recache: bool = False) -> str:
        url = canonical(address)
        if url is None:
            raise ValueError(f"{address!r} is not the address of a bdtheque.com series page")
        if not recache:
            stored = self.library.bdtheque_get(url)
            if stored is not None:
                return stored
        from bdnex.importer.bedetheque.bedetheque import BlockedError, looks_blocked

        logger.debug("Fetching %s", url)
        content = self._fetch(url)
        if looks_blocked(200, content):
            raise BlockedError(f"{url}: the fetcher returned an anti-bot challenge page, not the page. Nothing was stored.")
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
        parse_series_page(text, url)  # a page that is not a series page is not kept
        self.library.bdtheque_put(url, text)
        return text

    def album_info(self, address: str, volume: Optional[int] = None, recache: bool = False) -> AlbumInfo:
        return album_info(parse_series_page(self.page(address, recache), address), volume)


def create(library, settings) -> Bdtheque:
    """A reader of bdtheque.com with the fetcher of ``fetch`` (FlareSolverr and all its endpoints by default)."""
    from bdnex.fetchers import build_fetcher

    return Bdtheque(library, build_fetcher(settings))


def link_file(library, path: str, info: AlbumInfo) -> int:
    """The album in the library and the file linked to it (a file the library does not know is registered, read once). Returns the album id."""
    album_id = library.add_album(info)
    if library.get_file(path) is None:
        library.add_file(path, album_id)
    library.set_file_album(path, album_id)
    library.clear_review(path)
    return album_id
