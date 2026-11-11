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

# Credit labels that have no ComicInfo element: kept in the library under these roles
OTHER_CREDIT_ROLES = {"Traduction": "Translator", "Autres": "Other"}

# Bédéthèque label -> ComicInfo credit role
CREDIT_LABELS = {
    "Scénario": "Writer",
    "Dessin": "Penciller",
    "Couleurs": "Colorist",
    "Lettrage": "Letterer",
    "Couverture": "CoverArtist",
    "illustrator": "Inker",
}


# Desktop-layout role labels -> the legacy field names used by build_album_info
V2_ROLE_FIELDS = {"Scénario": "Scénario", "Dessin": "Dessin", "Couleurs": "Couleurs",
                  "Lettrage": "Lettrage", "Couverture": "Couverture"}


def parse_album_page(html: str, album_url: str) -> tuple[dict[str, Any], Optional[str]]:
    """Return the raw fields of an album page and its series url.

    Handles both the current desktop layout (schema.org microdata, ``bdt-ah``)
    and the former mobile layout (``<label>`` lists).
    """
    if "bdt-ah" in html:
        return _parse_album_page_desktop(html, album_url)
    return _parse_album_page_mobile(html, album_url)


def _meta(soup, prop):
    node = soup.find(attrs={"itemprop": prop})
    if node is None:
        return None
    return (node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)) or None


def _parse_album_page_desktop(html: str, album_url: str) -> tuple[dict[str, Any], Optional[str]]:
    soup = BeautifulSoup(html, "lxml")
    header = soup.select_one("section.bdt-ah") or soup
    fields: dict[str, Any] = {"album_url": album_url}

    h1 = header.find("h1")
    series_href = None
    if h1 is not None:
        fields["Titre"] = h1.get_text(strip=True)
        link = h1.find("a", href=True)
        series_href = link["href"] if link else None
        if fields["Titre"]:
            fields["Série"] = fields["Titre"]

    ident = header.select_one(".bdt-ah-idbel")
    match = re.search(r"-(\d+)\.html$", album_url)
    fields["Identifiant"] = (ident.get_text(strip=True) if ident else None) or (match.group(1) if match else "")

    volume = re.search(r"-Tome-(\d+)(?:-|\.html)", album_url)
    if volume:
        fields["Tome"] = volume.group(1)

    if (pub := _meta(header, "publisher")):
        fields["Editeur"] = pub
    if (isbn := _meta(header, "isbn")):
        fields["ISBN"] = isbn
    if (pages := _meta(header, "numberOfPages")):
        fields["Planches"] = pages
    if (date := _meta(header, "datePublished")):
        fields["Dépot_légal"] = date
    if (desc := _meta(header, "description")):
        fields["description"] = desc
    if (rating := _meta(header, "ratingValue")):
        try:
            fields["Note"] = float(rating)
        except ValueError:
            pass
    if (genre := _meta(header, "genre")):
        origin, _, rest = genre.partition(" - ")
        fields["Origine"] = origin if rest else ""
        fields["Style"] = rest or genre

    credits: dict[str, list[str]] = {}
    for span in header.select(".bdt-ah-auteurs > span"):
        name = span.find("a")
        role = span.find("small")
        if not name or not role:
            continue
        label = role.get_text(strip=True).strip("()")
        credits.setdefault(label, []).append(name.get_text(strip=True))
    for label, names in credits.items():
        if label in V2_ROLE_FIELDS:
            fields[V2_ROLE_FIELDS[label]] = "\n".join(names)
        else:
            fields.setdefault("autres_credits", {})[label] = names

    cover = header.select_one("a.bdt-img--couv[href]")
    if cover:
        fields["cover_url"] = cover["href"]

    edition = _edition_table(soup)
    if edition:
        fields["edition_fields"] = edition  # everything the edition table says, as written
        if edition.get("Format"):
            fields["Format"] = edition["Format"]
        for label in ("Traduction", "Autres"):
            names = _split_names(edition.get(label, ""))
            if names:
                fields.setdefault("autres_credits", {}).setdefault(label, names)
    note = soup.select_one("div.bdt-note > div")
    if note is not None:
        for label in note.find_all("b"):
            label.extract()
        text = " ".join(note.get_text(" ", strip=True).split())
        if text:
            fields["edition_info"] = text
    votes = _meta(header, "ratingCount")
    if votes and votes.isdigit():
        fields["rating_votes"] = int(votes)
    images = {kind: [a["href"] for a in soup.select(f"a.browse-{css}[href]")]
              for kind, css in (("plates", "planches"), ("versos", "versos"))}
    images = {kind: sorted(set(urls)) for kind, urls in images.items() if urls}
    if images:
        fields["image_urls"] = images
    return fields, series_href


def _edition_table(soup) -> dict[str, str]:
    """The label/value list of the album's edition (Format, Poids, Achevé impr., Traduction...)."""
    table = soup.select_one("article.bdt-edition--detail dl")
    if table is None:
        return {}
    rows = {}
    for term in table.find_all("dt"):
        value = term.find_next_sibling("dd")
        text = " ".join(value.get_text(" ", strip=True).split()) if value is not None else ""
        if text:
            rows[term.get_text(" ", strip=True)] = text
    return rows


def _parse_album_page_mobile(html: str, album_url: str) -> tuple[dict[str, Any], Optional[str]]:
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
    soup = BeautifulSoup(html, "lxml")
    node = soup.find(id="full-commentaire")
    if node is not None and node.get("value"):
        return node.attrs["value"]
    node = soup.find(attrs={"itemprop": "description"})
    return node.get_text(" ", strip=True) if node is not None else None


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

    for label, names in (fields.get("autres_credits") or {}).items():
        role = OTHER_CREDIT_ROLES.get(label, label)
        for name in names:
            if name not in credits.setdefault(role, []):
                credits[role].append(name)

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


def partial_album_info(candidate) -> AlbumInfo:
    """Minimal metadata taken from the sitemap URL alone (no page needed).

    ``BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html`` -> series ``Nains``, volume 1,
    title ``Redwin de la Forge``. One-shots (no ``-Tome-N-``) get the slug as title.
    """
    slug = candidate.url.rsplit("/", 1)[-1]
    slug = re.sub(r"^BD-", "", re.sub(r"\.html$", "", slug))
    slug = re.sub(r"-\d+$", "", slug)  # album id
    match = re.match(r"(?P<series>.+?)-Tome-(?P<vol>\d+)(?:-(?P<title>.*))?$", slug)
    if match:
        series_name = match["series"].replace("-", " ")
        title = (match["title"] or "").replace("-", " ") or series_name
        series = SeriesInfo(SOURCE, f"slug:{series_name.lower()}", series_name)
        return AlbumInfo(source=SOURCE, source_id=candidate.source_id, title=title, series=series,
                         volume=int(match["vol"]), language="fr", url=candidate.url,
                         extra={"partial": True})
    return AlbumInfo(source=SOURCE, source_id=candidate.source_id, title=slug.replace("-", " "),
                     language="fr", url=candidate.url, extra={"partial": True})
