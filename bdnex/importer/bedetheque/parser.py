"""Parse Bédéthèque mobile album/series pages into :class:`AlbumInfo`."""

from __future__ import annotations

import collections
import json
import logging
import re
import unicodedata
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


def _plain(text: str) -> str:
    """Letters and digits only, no accents, lower case: to compare an address with a title."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return re.sub(r"[\W_]+", "", "".join(c for c in decomposed if not unicodedata.combining(c)))


def _title_from_address(soup, album_url: str) -> str:
    """``BD-Aama-Tome-2-La-multitude-invisible-172621.html`` -> ``La multitude invisible``, or ``""``.

    The address has no accents, so the page is searched for the same words with them (picture titles, links); the
    words of the address are the fallback. An album with no title in its address (``…-Tome-1-19623.html``) has none.
    """
    match = re.search(r"-Tome-\d+-(.+?)-\d+\.html$", album_url)
    if not match:
        return ""
    words = match.group(1).replace("-", " ")
    wanted = _plain(words)
    for node in soup.find_all(["img", "a"]):
        for text in (node.get("alt"), node.get("title"), node.get_text(" ", strip=True)):
            text = re.sub(r"^Couverture de\s+", "", (text or "").strip())
            if text and _plain(text) == wanted:
                return text
    return words


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
            album_title = _title_from_address(soup, album_url)
            if album_title:  # the <h1> is the series; the album's own title is only in the address
                fields["Titre"] = album_title

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


META_KEYS = ("description", "keywords", "author", "category")
MAX_VALUE = 600  # a label's value longer than this is prose, not a field
STATUS_LABELS = ("statut", "etat", "etat de la serie", "status")
SPLIT_NAMES = re.compile(r"\s*[,/;]\s*")


def _clean(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def harvest(soup) -> dict[str, Any]:
    """Everything a page says about itself, without knowing its layout.

    * ``meta``: the Open Graph / Twitter / description tags;
    * ``microdata``: the schema.org ``itemprop`` values (``genre``, ``publisher``, ``aggregateRating``...);
    * ``jsonld``: any JSON-LD block;
    * ``fields``: ``label : value`` rows, from definition lists, table rows and ``<li><label>`` lines.

    Only what the page has appears; nothing is invented.
    """
    out: dict[str, Any] = {}
    meta = {}
    for tag in soup.find_all("meta"):
        key, content = tag.get("property") or tag.get("name"), _clean(tag.get("content"))
        if key and content and (key.startswith(("og:", "twitter:")) or key in META_KEYS):
            meta[key] = content
    if meta:
        out["meta"] = meta

    micro: dict[str, list[str]] = {}
    for tag in soup.find_all(attrs={"itemprop": True}):
        value = _clean(tag.get("content") or tag.get("href") or tag.get("src") or tag.get_text(" "))
        if value and len(value) <= MAX_VALUE and value not in micro.setdefault(tag["itemprop"], []):
            micro[tag["itemprop"]].append(value)
    micro = {k: v for k, v in micro.items() if v}
    if micro:
        out["microdata"] = micro

    blocks = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            blocks.append(json.loads(script.string or ""))
        except ValueError:
            continue
    if blocks:
        out["jsonld"] = blocks

    pairs: dict[str, str] = {}

    def add(label: str, value: str) -> None:
        label, value = _clean(label).rstrip(":").strip(), _clean(value)
        if label and value and len(label) <= 40 and len(value) <= MAX_VALUE and re.search(r"[^\W\d_]", label) \
                and not re.fullmatch(r"[\d\s/.\-:]+", label):  # '251' or '11/2021' is a value, not a label
            pairs[label] = f"{pairs[label]} | {value}" if label in pairs and value not in pairs[label] else pairs.get(label, value)

    for term in soup.find_all("dt"):
        definition = term.find_next_sibling("dd")
        if definition is not None:
            add(term.get_text(" "), definition.get_text(" "))
    for row in soup.find_all("tr"):
        head, cell = row.find("th"), row.find("td")
        if head is not None and cell is not None:
            add(head.get_text(" "), cell.get_text(" "))
    for item in soup.find_all("li"):
        label = item.find(["label", "strong", "b"])
        if label is not None:
            text = _clean(item.get_text(" "))
            name = _clean(label.get_text(" "))
            if text.startswith(name):
                add(name, text[len(name):].lstrip(" :"))
    if pairs:
        out["fields"] = pairs
    return out


def _normalised(harvested: dict[str, Any]) -> dict[str, Any]:
    """The few things worth having under a stable name, taken from the harvest when it is there."""
    micro, fields, meta = (harvested.get(k, {}) for k in ("microdata", "fields", "meta"))
    by_label = {fold_label(k): v for k, v in fields.items()}
    result: dict[str, Any] = {}
    genre = micro.get("genre") or ([by_label["genre"]] if "genre" in by_label else [by_label["style"]] if "style" in by_label else [])
    names = [name for value in genre for name in SPLIT_NAMES.split(value) if name]
    if names:
        result["genres"] = list(dict.fromkeys(names))
    for key, labels in (("status", STATUS_LABELS), ("origin", ("origine",)), ("language", ("langue",))):
        found = next((by_label[label] for label in labels if label in by_label), None)
        if key == "language" and not found and micro.get("inLanguage"):
            found = micro["inLanguage"][0]
        if found:
            result[key] = found
    if micro.get("ratingValue"):
        result["rating"] = micro["ratingValue"][0]
        if micro.get("ratingCount"):
            result["rating_count"] = micro["ratingCount"][0]
    if meta.get("og:image"):
        result["image"] = meta["og:image"]
    return result


def fold_label(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)).strip()


ALBUM_LINK = re.compile(r"/BD-(?P<slug>.+)-(?P<id>\d+)\.html$")


def parse_series_details(html: str) -> dict[str, Any]:
    """What a series page says: its ``summary``, the ``albums`` it lists, and everything else it carries.

    The rest is harvested without knowing the layout (see :func:`harvest`): ``meta``, ``microdata``,
    ``jsonld`` and ``fields``, plus a few normalised keys when found (``genres``, ``status``, ``origin``,
    ``language``, ``rating``, ``rating_count``, ``image``).

    The albums are read from the links, which follow the same ``BD-<series>-Tome-<n>-<title>-<id>.html``
    scheme in every layout: the series is the slug most links share before ``-Tome-``, and only its albums
    are kept (not the "similar series" links of the page). Each album has ``source_id``, ``url`` (on the
    ``www`` site), ``volume`` (``None`` for a special) and a ``title`` taken from the address.
    """
    details: dict[str, Any] = {}
    summary = parse_series_page(html)
    if summary:
        details["summary"] = summary
    soup = BeautifulSoup(html, "lxml")
    harvested = harvest(soup)
    details.update(harvested)
    details.update(_normalised(harvested))
    links = []
    for anchor in soup.find_all("a", href=True):
        match = ALBUM_LINK.search(anchor["href"])
        if match:
            links.append((match["slug"], match["id"], anchor["href"]))
    series_slug = collections.Counter(slug.split("-Tome-")[0] for slug, _, _ in links if "-Tome-" in slug)
    if not series_slug:
        return details
    prefix = series_slug.most_common(1)[0][0]
    albums, seen = [], set()
    for slug, ident, href in links:
        if ident in seen or not (slug == prefix or slug.startswith(prefix + "-")):
            continue
        seen.add(ident)
        tome = re.match(re.escape(prefix) + r"-Tome-(\d+)(?:-(.*))?$", slug)
        title = (tome.group(2) if tome else slug[len(prefix) + 1:]) or ""
        albums.append({"source_id": ident, "url": re.sub(r"://m\.", "://www.", href),
                       "volume": int(tome.group(1)) if tome else None, "title": title.replace("-", " ")})
    details["albums"] = albums
    return details


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
                     series_abstract: Optional[str] = None, series_extra: Optional[dict[str, Any]] = None) -> AlbumInfo:
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
        series = SeriesInfo(SOURCE, str(sid), series_name, summary=series_abstract, url=series_url,
                            extra=dict(series_extra or {}))

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
    if match and int(match["vol"]) == 0 and not match["title"]:
        slug = match["series"]
        match = None  # "Tome 0" with nothing else is how some one-shots are listed: it is no series
    if match:
        series_name = match["series"].replace("-", " ")
        title = (match["title"] or "").replace("-", " ") or series_name
        series = SeriesInfo(SOURCE, f"slug:{series_name.lower()}", series_name)
        return AlbumInfo(source=SOURCE, source_id=candidate.source_id, title=title, series=series,
                         volume=int(match["vol"]), language="fr", url=candidate.url,
                         extra={"partial": True})
    return AlbumInfo(source=SOURCE, source_id=candidate.source_id, title=slug.replace("-", " "),
                     language="fr", url=candidate.url, extra={"partial": True})
