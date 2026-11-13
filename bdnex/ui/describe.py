"""Human-readable descriptions of albums, for `bdnex import` and `bdnex ls --long`."""

from __future__ import annotations

import os
from typing import Any, Optional

ROLE_ORDER = ("Writer", "Penciller", "Inker", "Colorist", "Letterer", "CoverArtist")


def label(album: dict[str, Any]) -> str:
    if album.get("series") and album.get("volume") is not None:
        return f"{album['series']} - T{int(album['volume']):02d} - {album['title']}"
    if album.get("series") and album.get("alternate_number"):
        return f"{album['series']} - {album['alternate_number']} - {album['title']}"
    return album["title"]


def credits_line(album: dict[str, Any]) -> str:
    credits = album.get("credits") or {}
    return " · ".join(f"{role}: {', '.join(credits[role])}" for role in ROLE_ORDER if credits.get(role))


def completeness(album: dict[str, Any]) -> str:
    return "matched, details not fetched (`bdnex import --fetch`)" if (album.get("extra") or {}).get("partial") else "complete"


def short(album: dict[str, Any]) -> str:
    """One line: what a user wants to see right after an import."""
    parts = [f"{label(album)} ({album['kind']})"]
    if album.get("credits"):
        names = sorted({n for names in album["credits"].values() for n in names})
        parts.append("; ".join(names[:4]) + ("…" if len(names) > 4 else ""))
    published = " ".join(str(x) for x in (album.get("publisher"), album.get("year")) if x)
    if published:
        parts.append(published)
    if album.get("pages"):
        parts.append(f"{album['pages']} pages")
    if album.get("genres"):
        parts.append(", ".join(album["genres"]))
    parts.append("complete" if completeness(album) == "complete" else "PARTIAL")
    return " · ".join(parts)


def date_of(album: dict[str, Any]) -> Optional[str]:
    if not album.get("year"):
        return None
    return "-".join(f"{int(album[k]):02d}" if k != "year" else str(album[k])
                    for k in ("year", "month", "day") if album.get(k))


def size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def long(album: dict[str, Any], files: list[dict[str, Any]]) -> str:
    """All the metadata of an album and the files linked to it."""
    lines = [f"{label(album)}   [{album['kind']}, {completeness(album)}]"]

    def add(name: str, value: Any) -> None:
        if value not in (None, "", [], {}):
            lines.append(f"  {name:<13}{value}")

    if album.get("series"):
        add("series", album["series"])
    add("volume", album.get("volume") if album.get("volume") is not None else album.get("alternate_number"))
    add("title", album["title"])
    credits = album.get("credits") or {}
    for role in [*ROLE_ORDER, *sorted(set(credits) - set(ROLE_ORDER))]:  # standard roles, then Translator, Other...
        add(role.lower(), ", ".join(credits.get(role, [])))
    add("publisher", album.get("publisher"))
    add("published", date_of(album))
    add("pages", album.get("pages"))
    add("genres", ", ".join(album.get("genres") or []))
    add("rating", f"{album['rating']} / 5" if album.get("rating") is not None else None)
    add("isbn", album.get("isbn"))
    add("format", album.get("format"))
    extra = album.get("extra") or {}
    edition = extra.get("edition_fields") or {}
    add("weight", edition.get("Poids"))
    add("printed", edition.get("Achevé impr."))
    add("edition", extra.get("edition_info"))
    add("language", album.get("language"))
    add("source", f"{album['source']} #{album['source_id']}")
    add("url", album.get("url"))
    summary = (album.get("summary") or "").strip().replace("\n", " ")
    add("summary", summary[:240] + ("…" if len(summary) > 240 else ""))
    for f in files:
        flags = ", ".join(x for x in ("corrupted" if f["corrupted"] else "", "has ComicInfo.xml" if f["has_metadata"] else "") if x)
        lines.append(f"  {'file':<13}{f['path']}")
        lines.append(f"  {'':<13}{f['format']}, {f['page_count'] or '?'} pages, {size(f['size'])}"
                     + (f"  ({flags})" if flags else ""))
        if f.get("embedded"):
            lines.append(f"  {'':<13}{embedded_line(f['embedded'])}")
    if not files:
        lines.append(f"  {'file':<13}(none linked)")
    return "\n".join(lines)


def embedded_line(embedded) -> str:
    """What a file's own ComicInfo.xml says, in one line (empty when it has none)."""
    if not embedded:
        return ""
    series, number, title = embedded.get("Series"), embedded.get("Number") or embedded.get("Volume"), embedded.get("Title")
    year = embedded.get("Year")
    text = " - ".join(x for x in (series, f"#{number}" if number else None, title) if x)
    return f"in the file: {text}" + (f" ({year})" if year else "") if text else "in the file: a ComicInfo.xml without series or title"


def file_block(row: dict[str, Any], album_label: str) -> str:
    flags = ", ".join(x for x in ("corrupted" if row["corrupted"] else "", "has ComicInfo.xml" if row["has_metadata"] else "") if x)
    return (f"{row['path']}\n  {row['format']}, {row['page_count'] or '?'} pages, {size(row['size'])}"
            + (f"  ({flags})" if flags else "") + f"\n  album: {album_label}"
            + (f"\n  {embedded_line(row.get('embedded'))}" if row.get("embedded") else "") + f"\n  md5: {row['md5']}")


__all__ = ["label", "short", "long", "file_block", "credits_line", "completeness", "os"]
