"""Plain data objects exchanged between metadata source plugins and the library.

A metadata source plugin turns whatever it scrapes into an :class:`AlbumInfo`
and hands it to :meth:`bdnex.library.Library.add_album`. This is the contract
documented in the plugin guide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Credit roles use the ComicInfo.xml vocabulary.
CREDIT_ROLES = ("Writer", "Penciller", "Inker", "Colorist", "Letterer", "CoverArtist")

KIND_SERIES = "series"
KIND_ONESHOT = "oneshot"


@dataclass
class SeriesInfo:
    source: str
    source_id: str
    name: str
    summary: Optional[str] = None
    url: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. ``albums``: every album the source lists for it


@dataclass
class AlbumInfo:
    source: str  # plugin name, e.g. "bedetheque"
    source_id: str  # identifier of the album on that source
    title: str
    series: Optional[SeriesInfo] = None  # None => one-shot
    volume: Optional[int] = None
    alternate_number: Optional[str] = None  # "Hors-Série", "Intégrale"...
    publisher: Optional[str] = None
    isbn: Optional[str] = None
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    pages: Optional[int] = None
    language: Optional[str] = None
    format: Optional[str] = None
    subseries: Optional[str] = None  # a hand-set subfolder inside the series, for an album the site does not list
    collection: Optional[str] = None  # a hand-set override of the site's own "Collection" (extra.edition_fields)
    cycle: Optional[str] = None  # a hand-set override of the site's own "Cycle" (extra.edition_fields)
    category: Optional[str] = None  # a hand-set kind of publication (BD, Manga, Comics...): `$category` in the path templates
    summary: Optional[str] = None
    rating: Optional[float] = None
    cover_url: Optional[str] = None
    url: Optional[str] = None
    credits: dict[str, list[str]] = field(default_factory=dict)  # role -> names
    genres: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # raw source fields

    @property
    def kind(self) -> str:
        return KIND_SERIES if self.series is not None else KIND_ONESHOT


# What the library called a credit role before the source's labels were mapped: read back as the new name
LEGACY_ROLES = {"Encrage": "Inker"}


SITE_KEYS = {"collection": "Collection", "cycle": "Cycle"}  # fields the site's edition table also gives (extra.edition_fields)


def effective(album: dict, key: str) -> str:
    """The value that is USED for ``collection`` / ``cycle``: what you set by hand (``""`` is a value: no collection at all), else the site's own."""
    hand = album.get(key)
    if hand is not None:
        return hand
    edition = (album.get("extra") or {}).get("edition_fields") or {}
    return str(edition.get(SITE_KEYS[key]) or "")


def to_store(album: dict, key: str, new: Optional[str]) -> tuple[bool, Optional[str]]:
    """What the form's ``new`` value means for ``collection`` / ``cycle``: ``(changed, value to store)``. The form shows the value in use, so
    the same text is no change; an emptied field is an explicit ``""`` (none) when the site has a value, else just no value."""
    if (new or "") == effective(album, key):
        return False, None
    if new:
        return True, new
    site = effective({**album, key: None}, key)
    return True, "" if site else None
