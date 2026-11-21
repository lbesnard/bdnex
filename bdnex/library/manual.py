"""Albums written by hand, for a comic the site does not know (``source`` = ``manual``): a form, the albums it makes, the file linked.

The review screen's ``e`` key fills the form for one file (or for several files of one series). Nothing here fetches anything;
the album is an ordinary library album, so ``bdnex write`` / ``ls`` / ``check`` treat it like any other.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any, Optional

from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.library.query import fold

MANUAL = "manual"

# the form: key, label, hint
FIELDS = (
    ("series", "Series", "empty = a one-shot"),
    ("volume", "Volume", "a number"),
    ("title", "Title", "empty = the series and the volume"),
    ("publisher", "Publisher", ""),
    ("year", "Year", "four digits"),
    ("writer", "Writer(s)", "separate with , or ;"),
    ("artist", "Artist(s)", "separate with , or ;"),
    ("genres", "Genres", "separate with , or ;"),
    ("summary", "Summary", ""),
)
SHARED = ("series", "publisher", "year", "writer", "artist", "genres", "summary")  # what files of one series have in common


class FormError(ValueError):
    """What is wrong with the form, in words."""


def _names(text: str) -> list[str]:
    return [name.strip() for name in re.split(r"[;,]", text or "") if name.strip()]


def _number(text: str, what: str, low: int, high: int) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    if not text.isdigit() or not low <= int(text) <= high:
        raise FormError(f"{what} must be a number from {low} to {high}")
    return int(text)


def guess(path: str) -> dict[str, str]:
    """A first form for a file, from its folder and name: the series is the folder, the volume the number in the name."""
    from bdnex.importer.folders import title_and_volume

    _, volume = title_and_volume(path)
    folder = os.path.basename(os.path.dirname(path))
    return {"series": folder, "volume": "" if volume is None else str(volume)}


def form_of(library: Library, album_id: int) -> dict[str, str]:
    """The form of an album already written by hand (to edit it)."""
    album = library.get_album(album_id) or {}
    credits = album.get("credits") or {}
    return {"series": album.get("series") or "", "volume": "" if album.get("volume") is None else str(album["volume"]),
            "title": album.get("title") or "", "publisher": album.get("publisher") or "",
            "year": "" if album.get("year") is None else str(album["year"]),
            "writer": ", ".join(credits.get("Writer", [])), "artist": ", ".join(credits.get("Penciller", [])),
            "genres": ", ".join(album.get("genres") or []), "summary": album.get("summary") or ""}


def is_manual(library: Library, album_id: Optional[int]) -> bool:
    return album_id is not None and (library.get_album(album_id) or {}).get("source") == MANUAL


def _info(values: dict[str, str], path: str, volume: Optional[int]) -> AlbumInfo:
    series = (values.get("series") or "").strip()
    title = (values.get("title") or "").strip()
    if not title:
        title = (f"{series} - T{volume:02d}" if series and volume is not None else series
                 or os.path.splitext(os.path.basename(path))[0])
    credits = {role: names for role, names in (("Writer", _names(values.get("writer", ""))),
                                               ("Penciller", _names(values.get("artist", "")))) if names}
    return AlbumInfo(
        MANUAL, uuid.uuid4().hex[:12], title,
        series=SeriesInfo(MANUAL, "manual-" + re.sub(r"[^a-z0-9]+", "-", fold(series)).strip("-"), series) if series else None,
        volume=volume, publisher=(values.get("publisher") or "").strip() or None,
        year=_number(values.get("year", ""), "the year", 1000, 9999), summary=(values.get("summary") or "").strip() or None,
        credits=credits, genres=_names(values.get("genres", "")))


def create(library: Library, path: str, values: dict[str, str], volume: Optional[int] = None) -> int:
    """A new album from the form, linked to the file (the file keeps no other album). Returns the album id."""
    if volume is None:
        volume = _number(values.get("volume", ""), "the volume", 0, 9999)
    album_id = library.add_album(_info(values, path, volume))
    if library.get_file(path) is None:  # a file the import skipped has no record yet: register it (read once) with the link
        library.add_file(path, album_id)
    library.set_file_album(path, album_id)
    library.clear_review(path)
    return album_id


def update(library: Library, album_id: int, values: dict[str, str]) -> None:
    """Change an album written by hand from the form (a new series name renames the series of all its manual albums)."""
    album = library.get_album(album_id) or {}
    new = _info(values, "", _number(values.get("volume", ""), "the volume", 0, 9999))
    changes: dict[str, Any] = {"title": new.title if (values.get("title") or "").strip() else album.get("title"),
                               "volume": new.volume, "publisher": new.publisher, "year": new.year, "summary": new.summary,
                               "genres": new.genres, "credits": {"Writer": new.credits.get("Writer", []),
                                                                 "Penciller": new.credits.get("Penciller", [])}}
    if album.get("kind") == "series" and new.series is not None and new.series.name != album.get("series"):
        changes["series"] = new.series.name
    library.edit_album(album_id, changes)


def create_series(library: Library, files: list[tuple[str, Optional[int]]], values: dict[str, str]) -> list[int]:
    """One album per file, all of one series with what the form says, each with its own volume. ``files``: (path, volume)."""
    if not (values.get("series") or "").strip():
        raise FormError("a series needs its name")
    done = []
    for path, volume in files:
        done.append(create(library, path, {**values, "title": "", "volume": ""}, volume))
    return done
