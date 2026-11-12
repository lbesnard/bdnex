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
