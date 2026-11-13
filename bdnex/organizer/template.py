"""Path templates: ``Series/$series/$series - T$volume:02 - $title``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

VAR = re.compile(r"\$(?:\{(?P<b>\w+)\}|(?P<n>\w+))(?::(?P<pad>\d+))?")
ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_COMPONENT = 120


class MissingField(Exception):
    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


def sanitize(value: str) -> str:
    """Make one path component safe on every platform."""
    value = ILLEGAL.sub("_", str(value))
    value = re.sub(r"\s+", " ", value).strip().rstrip(". ")
    return value[:MAX_COMPONENT].rstrip(". ") or "_"


def fields_for(album: dict[str, Any]) -> dict[str, Any]:
    """Template variables for an album (``Library.get_album``)."""
    credits = album["credits"]
    authors = []
    for role in ("Writer", "Penciller"):
        for name in credits.get(role, []):
            if name not in authors:
                authors.append(name)
    volume = album.get("volume")
    return {
        "kind": album["kind"],
        "series": album.get("series") or "",
        "title": album["title"],
        "volume": volume if volume is not None else (album.get("alternate_number") or ""),
        "language": album.get("language") or "",
        "year": album.get("year") or "",
        "publisher": album.get("publisher") or "",
        "isbn": album.get("isbn") or "",
        "genre": (album["genres"] or [""])[0],
        "authors": ", ".join(authors),
        "writer": ", ".join(credits.get("Writer", [])),
        "penciller": ", ".join(credits.get("Penciller", [])),
        "colorist": ", ".join(credits.get("Colorist", [])),
    }


FIELD_NAMES = ("kind", "series", "title", "volume", "language", "year", "publisher", "isbn", "genre", "authors",
               "writer", "penciller", "colorist")  # what `fields_for` provides (a test keeps the two together)


def unknown_fields(template: str) -> list[str]:
    """The ``$variables`` of a template that bdnex does not know (a typo, usually)."""
    return sorted({name for name in template_fields(template) if name not in FIELD_NAMES})


def template_fields(template: str) -> list[str]:
    return [m.group("b") or m.group("n") for m in VAR.finditer(template)]


@dataclass
class Rendered:
    parts: list[str]  # sanitised path components (no extension)
    skipped_field: Optional[str] = None


def render(template: str, values: dict[str, Any], resolve_missing: Callable[[str], Optional[str]]) -> Rendered:
    """Render a template into path components.

    ``resolve_missing(field)`` is called for an empty field and returns the value to
    use, or ``None`` to skip the file. Values are sanitised, so a ``/`` inside a
    value can never create a directory.
    """
    cache: dict[str, Optional[str]] = {}

    def substitute(match: re.Match) -> str:
        name = match.group("b") or match.group("n")
        value = values.get(name, "")
        if value in ("", None):
            if name not in cache:
                cache[name] = resolve_missing(name)
            if cache[name] is None:
                raise MissingField(name)
            value = cache[name]
        pad = match.group("pad")
        if pad and str(value).isdigit():
            value = str(int(value)).zfill(int(pad))
        return sanitize(value)

    parts = []
    try:
        for component in template.split("/"):
            rendered = VAR.sub(substitute, component).strip()
            if rendered:
                parts.append(sanitize(rendered))
    except MissingField as err:
        return Rendered([], skipped_field=err.field)
    return Rendered(parts)
