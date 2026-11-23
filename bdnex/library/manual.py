"""Albums written by hand, for a comic the site does not know (``source`` = ``manual``): a form, the albums it makes, the file linked.

The review screen's ``e`` key fills the form for one file (or for several files of one series). Nothing here fetches anything;
the album is an ordinary library album, so ``bdnex write`` / ``ls`` / ``check`` treat it like any other.
"""

from __future__ import annotations

import dataclasses
import os
import re
import uuid
from typing import Any, Optional

from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.library.models import CREDIT_ROLES
from bdnex.library.query import fold

MANUAL = "manual"

# the credit roles of the form: their key (lower case, as typed) -> one of CREDIT_ROLES ("artist" is bdnex's own word for Penciller)
CREDIT_KEYS = {"writer": "Writer", "artist": "Penciller", "inker": "Inker", "colorist": "Colorist",
              "letterer": "Letterer", "coverartist": "CoverArtist"}

# the form: key, label, hint
FIELDS = (
    ("series", "Series", "empty = a one-shot"),
    ("volume", "Volume", "a number"),
    ("alternate_number", "Special", "Hors-série, Intégrale... when there is no number"),
    ("title", "Title", "empty = the series and the volume"),
    ("subseries", "Subfolder", "a subfolder inside the series, for an album the site does not list"),
    ("collection", "Collection", "overrides the site's own Collection, and any paths.collections rule"),
    ("cycle", "Cycle", "overrides the site's own Cycle"),
    ("publisher", "Publisher", ""),
    ("isbn", "ISBN", ""),
    ("year", "Year", "four digits"),
    ("month", "Month", "1 to 12"),
    ("day", "Day", "1 to 31"),
    ("language", "Language", "a code, e.g. fr"),
    ("format", "Format", ""),
    ("pages", "Pages", "a number"),
    ("writer", "Writer(s)", "separate with , or ;"),
    ("artist", "Artist(s)", "separate with , or ;"),
    ("inker", "Inker(s)", "separate with , or ;"),
    ("colorist", "Colorist(s)", "separate with , or ;"),
    ("letterer", "Letterer(s)", "separate with , or ;"),
    ("coverartist", "Cover artist(s)", "separate with , or ;"),
    ("genres", "Genres", "separate with , or ;"),
    ("summary", "Summary", ""),
)
# what files of one series have in common (volume, the special tag, title, subfolder, cycle, isbn, pages, month and day vary file to file)
SHARED = ("series", "collection", "publisher", "year", "language", "format", *CREDIT_KEYS, "genres", "summary")


class FormError(ValueError):
    """What is wrong with the form, in words."""


def _names(text: str) -> list[str]:
    """Names typed in a form: separated by `;` when there is one (so "Dupuy, Philippe" stays one person), else by `,`."""
    text = text or ""
    return [name.strip() for name in re.split(r"[;]" if ";" in text else r"[,]", text) if name.strip()]


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


def _str(album: dict[str, Any], key: str) -> str:
    return "" if album.get(key) is None else str(album[key])


def form_of(library: Library, album_id: int) -> dict[str, str]:
    """The form of an album already written by hand (to edit it)."""
    album = library.get_album(album_id) or {}
    credits = album.get("credits") or {}
    values = {key: _str(album, key) for key in ("series", "volume", "alternate_number", "title", "subseries", "collection", "cycle",
                                                  "publisher", "isbn", "year", "month", "day", "language", "format", "pages", "summary")}
    values.update({key: "; ".join(credits.get(role, [])) for key, role in CREDIT_KEYS.items()})
    values["genres"] = "; ".join(album.get("genres") or [])
    return values


def is_manual(library: Library, album_id: Optional[int]) -> bool:
    return album_id is not None and (library.get_album(album_id) or {}).get("source") == MANUAL


def series_key(name: str) -> str:
    """The source id of a series written by hand: a readable slug and a short hash of the exact name, so that "C++" and "C#"
    (or two names made only of symbols) are two series, and one name is always one series."""
    import hashlib

    slug = re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")
    return f"manual-{slug}-{hashlib.sha1(name.strip().casefold().encode()).hexdigest()[:6]}"


def _text(values: dict[str, str], key: str) -> Optional[str]:
    return (values.get(key) or "").strip() or None


def _info(values: dict[str, str], path: str, volume: Optional[int]) -> AlbumInfo:
    series = (values.get("series") or "").strip()
    title = (values.get("title") or "").strip()
    if not title:
        title = (f"{series} - T{volume:02d}" if series and volume is not None else series
                 or os.path.splitext(os.path.basename(path))[0])
    credits = {role: _names(values.get(key, "")) for key, role in CREDIT_KEYS.items()}
    credits = {role: names for role, names in credits.items() if names}
    return AlbumInfo(
        MANUAL, uuid.uuid4().hex[:12], title,
        series=SeriesInfo(MANUAL, series_key(series), series) if series else None,
        volume=volume, alternate_number=_text(values, "alternate_number"),
        publisher=_text(values, "publisher"), isbn=_text(values, "isbn"),
        year=_number(values.get("year", ""), "the year", 1000, 9999),
        month=_number(values.get("month", ""), "the month", 1, 12),
        day=_number(values.get("day", ""), "the day", 1, 31),
        pages=_number(values.get("pages", ""), "the pages", 0, 99999),
        language=_text(values, "language"), format=_text(values, "format"), subseries=_text(values, "subseries"),
        collection=_text(values, "collection"), cycle=_text(values, "cycle"),
        summary=_text(values, "summary"), credits=credits, genres=_names(values.get("genres", "")))


def check(values: dict[str, str], series: bool = False) -> None:
    """Raise :class:`FormError` for what :func:`create` / :func:`update` would refuse, without touching the library: a form can say so
    before it closes (a bad year used to be found after the form was gone, and everything typed was lost)."""
    if series and not (values.get("series") or "").strip():
        raise FormError("a series needs its name")
    _info(values, "", _number(values.get("volume", ""), "the volume", 0, 9999))


def create(library: Library, path: str, values: dict[str, str], volume: Optional[int] = None) -> int:
    """A new album from the form, linked to the file (the file keeps no other album). Returns the album id."""
    if volume is None:
        volume = _number(values.get("volume", ""), "the volume", 0, 9999)
    info = _info(values, path, volume)
    if info.series is not None:  # a series already written by hand under this name (an older id, or one renamed to it) is the one
        row = library.conn.execute("SELECT source_id FROM series WHERE source = ? AND name = ? COLLATE NOCASE",
                                   (MANUAL, info.series.name)).fetchone()
        if row:
            info = dataclasses.replace(info, series=dataclasses.replace(info.series, source_id=row["source_id"]))
    album_id = library.add_album(info)
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
                               "volume": new.volume, "alternate_number": new.alternate_number, "publisher": new.publisher,
                               "isbn": new.isbn, "year": new.year, "month": new.month, "day": new.day, "pages": new.pages,
                               "language": new.language, "format": new.format, "subseries": new.subseries,
                               "collection": new.collection, "cycle": new.cycle,
                               "summary": new.summary, "genres": new.genres,
                               "credits": {role: new.credits.get(role, []) for role in CREDIT_ROLES}}
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
