"""Parse Bédéthèque mobile album/series pages into :class:`AlbumInfo`."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

import bs4
import dateutil.parser
from bs4 import BeautifulSoup

from bdnex.library.models import AlbumInfo, SeriesInfo

SOURCE = "bedetheque"

logger = logging.getLogger(__name__)

# Bédéthèque label -> ComicInfo credit role
CREDIT_LABELS = {
    "Scénario": "Writer",
    "Dessin": "Penciller",
    "Couleurs": "Colorist",
    "Lettrage": "Letterer",
    "Couverture": "CoverArtist",
    "illustrator": "Inker",
}


def parse_album_page(html: str, album_url: str) -> tuple[dict[str, Any], Optional[str]]:
    """Return the raw ``label -> value`` fields of an album page and its series url."""
    soup = BeautifulSoup(html, "lxml")
    fields: dict[str, Any] = {"album_url": album_url}
    series_href = None

    for label in soup.select("label"):
        if not label.contents:
            continue
        try:
            key = label.contents[0].split(":")[0].rstrip().replace(" ", "_")
            if "Note" in key:
                val = label.find_parent().contents[8]
                val = float(re.search(r"(\d+.*)/", val).group()[:-1])
            elif label.find_next_sibling():
                val = label.find_next_sibling().text.rstrip()
            elif "Dépot" in key:
                val = label.find_parent().contents[2]
            else:
                val = label.find_parent().contents[1]

            if key == "Série":
                try:
                    series_href = label.find_parent().find_all(href=True)[0].get("href")
                except Exception:
                    pass
            fields[key] = val
        except Exception:
            continue

    imgs = soup.find_all("img", alt=True)
    if len(imgs) > 1:
        fields["cover_url"] = imgs[1].attrs["src"]

    for span in soup.find_all("span", attrs={"class": "infoedition"}):
        if "Résumé" in span.contents[0].contents[0]:
            fields["description"] = span.contents[1]

    for key, val in list(fields.items()):
        if isinstance(val, bs4.element.Tag):
            logger.error(f"{key} wrongly parsed and dropped")
            del fields[key]
        elif isinstance(val, str):
            fields[key] = val.strip()
        elif isinstance(val, bs4.element.NavigableString):
            fields[key] = str(val).strip()

    return fields, series_href


def parse_series_page(html: str) -> Optional[str]:
    """Return the series abstract, if any."""
    node = BeautifulSoup(html, "lxml").find(id="full-commentaire")
    return node.attrs.get("value") if node is not None else None


def series_id_from_url(url: str) -> Optional[str]:
    match = re.search(r"serie-(\d+)", url or "")
    return match.group(1) if match else None


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return dateutil.parser.parse(raw)
    except (dateutil.parser.ParserError, OverflowError):
        pass
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", raw)
    if match:
        d, m, y = (int(x) for x in match.groups())
        try:
            return datetime(y, m, d)
        except ValueError:
            return None
    return None


def _split_names(value: str) -> list[str]:
    return [v.strip() for v in re.split(r"[\n;]+", value) if v.strip()]


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def build_album_info(fields: dict[str, Any], series_url: Optional[str] = None,
                     series_abstract: Optional[str] = None) -> AlbumInfo:
    """Convert raw page fields to an :class:`AlbumInfo`.

    An album is a one-shot when it has no ``Tome`` number and is not a numbered-less
    special (``Hors-Série``, ``Intégrale``...) of a series.
    """
    volume, alternate = None, None
    tome = fields.get("Tome")
    if isinstance(tome, str) and tome:
        if tome[0].isdigit():
            nums = [p for p in re.split(r"(\d+|\s+)", tome) if p and not p.isspace()]
            volume = _int(nums[-1])
        else:
            alternate = tome
    elif isinstance(tome, int):
        volume = tome

    series = None
    series_name = fields.get("Série")
    if series_name and (volume is not None or alternate is not None):
        sid = series_id_from_url(series_url) or series_name
        series = SeriesInfo(SOURCE, str(sid), series_name, summary=series_abstract, url=series_url)

    published = _parse_date(fields.get("Dépot_légal"))

    summary = fields.get("description")
    if series_abstract and summary:
        summary = f"{series_abstract}\n {summary}"
    elif series_abstract:
        summary = series_abstract

    credits: dict[str, list[str]] = {}
    for label, role in CREDIT_LABELS.items():
        if fields.get(label):
            credits.setdefault(role, []).extend(_split_names(fields[label]))

    source_id = fields.get("Identifiant") or re.search(r"-(\d+)\.html$", fields["album_url"]).group(1)
    note = fields.get("Note")
    genres = [g.strip() for g in re.split(r"[,/]", fields["Style"]) if g.strip()] if fields.get("Style") else []

    return AlbumInfo(
        source=SOURCE, source_id=str(source_id), title=fields.get("Titre", ""),
        series=series, volume=volume, alternate_number=alternate,
        publisher=fields.get("Editeur") or None, isbn=fields.get("ISBN") or None,
        year=published.year if published else None,
        month=published.month if published else None,
        day=published.day if published else None,
        pages=_int(fields.get("Planches")), language="fr",
        format=fields.get("Format") or None, summary=summary,
        rating=note if isinstance(note, float) else None,
        cover_url=fields.get("cover_url"), url=fields["album_url"],
        credits=credits, genres=genres, extra=fields)
