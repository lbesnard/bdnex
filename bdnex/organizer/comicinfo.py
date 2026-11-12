"""Build, read, validate and diff ``ComicInfo.xml``."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Optional

import xmlschema

from bdnex.config import CONF_DIR

SCHEMA_PATH = CONF_DIR / "ComicInfo.xsd"
CREDIT_FIELDS = ("Writer", "Penciller", "Inker", "Colorist", "Letterer", "CoverArtist")


def album_to_fields(album: dict[str, Any], page_count: Optional[int] = None) -> dict[str, str]:
    """Map a library album (``Library.get_album``) to ComicInfo fields (all strings).

    One-shots use their title as ``Series`` so readers group them sensibly. A series
    album's volume goes to both ``Number`` (what readers sort on) and ``Volume``.
    """
    fields: dict[str, str] = {"Title": album["title"]}
    fields["Series"] = album["series"] or album["title"]
    if album.get("volume") is not None:
        fields["Number"] = str(album["volume"])
        fields["Volume"] = str(album["volume"])
    if album.get("alternate_number"):
        fields["AlternateNumber"] = album["alternate_number"]
    for key, name in (("year", "Year"), ("month", "Month"), ("day", "Day")):
        if album.get(key):
            fields[name] = str(album[key])
    for role in CREDIT_FIELDS:
        if album["credits"].get(role):
            fields[role] = ", ".join(album["credits"][role])
    for key, name in (("summary", "Summary"), ("publisher", "Publisher"), ("url", "Web"),
                      ("language", "LanguageISO"), ("format", "Format")):
        if album.get(key):
            fields[name] = str(album[key])
    if album["genres"]:
        fields["Genre"] = ", ".join(album["genres"])
    if album.get("rating") is not None:
        fields["CommunityRating"] = f"{min(5.0, max(0.0, float(album['rating']))):.2f}"
    notes = []
    if album.get("isbn"):
        notes.append(f"ISBN: {album['isbn']}")
    if (album.get("extra") or {}).get("edition_info"):
        notes.append(f"Édition: {album['extra']['edition_info']}")
    if notes:
        fields["Notes"] = "\n".join(notes)
    pages = page_count if page_count is not None else album.get("pages")
    if pages:
        fields["PageCount"] = str(pages)
    return fields


def build_xml(fields: dict[str, str]) -> bytes:
    """Serialise and validate against the packaged ComicInfo schema."""
    root = ET.Element("ComicInfo")
    for name, value in fields.items():
        ET.SubElement(root, name).text = value
    ET.indent(root)
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    xmlschema.XMLSchema(str(SCHEMA_PATH)).validate(data.decode("utf-8"))
    return data


def parse_xml(data: bytes) -> dict[str, str]:
    """Read the simple text fields of a ComicInfo.xml (ignores ``Pages`` and unknown nesting)."""
    root = ET.fromstring(data)
    return {child.tag: (child.text or "").strip() for child in root if len(child) == 0 and (child.text or "").strip()}


WIDTH = 100  # longest value shown in full; longer ones are shown around their first difference


def squash(value: Optional[str]) -> Optional[str]:
    """Collapse newlines and runs of spaces: a summary of 1000 characters is one line, not twenty."""
    return None if value is None else " ".join(value.split())


def excerpts(before: Optional[str], after: Optional[str], width: int = WIDTH) -> tuple[Optional[str], Optional[str], str]:
    """Short versions of two values, centred on where they first differ. Returns (before, after, note)."""
    before, after = squash(before), squash(after)
    if max(len(before or ""), len(after or "")) <= width:
        return before, after, ""
    if before is None or after is None:  # only one side exists: show its start
        value = before if after is None else after
        cut = value[:width] + "…"
        return (cut, None, f"({len(value)} characters)") if after is None else (None, cut, f"({len(value)} characters)")
    first = next((i for i, (x, y) in enumerate(zip(before, after)) if x != y), min(len(before), len(after)))
    start = max(0, first - 30)

    def window(text: str) -> str:
        piece = text[start:start + width]
        return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")

    return window(before), window(after), f"({len(before)} -> {len(after)} characters, first difference at {first})"


class Change(str):
    """One difference. As a string it is the plain one-line form (``~ Title: A  ->  B``); the parts are
    attributes, so a terminal can show it in colour."""

    kind: str  # "+" the database has a value the file lacks, "~" both have a value and they differ
    field: str
    before: Optional[str]  # shortened value in the file
    after: Optional[str]  # shortened value in the database
    note: str

    def __new__(cls, kind: str, field: str, before: Optional[str], after: Optional[str]):
        before, after, note = excerpts(before, after)
        if kind == "+":
            text = f"+ {field}: {after}"
        else:
            text = f"~ {field}: {before}  ->  {after}"
        obj = super().__new__(cls, text + (f"  {note}" if note else ""))
        obj.kind, obj.field, obj.before, obj.after, obj.note = kind, field, before, after, note
        return obj


def diff_fields(old: dict[str, str], new: dict[str, str]) -> list[Change]:
    """What writing the database's values (``new``) would change in a file's ComicInfo (``old``).

    A field the database has no value for is never a difference: it is kept, not erased.
    """
    changes = []
    for name in sorted(new):
        before, after = old.get(name), new[name]
        if before != after:
            changes.append(Change("+" if before is None else "~", name, before, after))
    return changes


def merge_with_existing(existing: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """The database's values win; everything else already in the file is kept."""
    return {**existing, **new}


def fields_to_changes(values: dict[str, str]) -> dict[str, Any]:
    """The album edits (``Library.edit_album``) that make the library say what these ComicInfo fields say.

    Fields with no home in the library (page count, rating, notes, web address) and ``Series`` (renaming it
    would rename the series of every album) are left out.
    """
    changes: dict[str, Any] = {}
    for field, key in (("Title", "title"), ("Summary", "summary"), ("Publisher", "publisher"),
                       ("LanguageISO", "language"), ("Format", "format"), ("AlternateNumber", "alternate_number")):
        if field in values:
            changes[key] = values[field]
    for field, key in (("Year", "year"), ("Month", "month"), ("Day", "day"), ("Volume", "volume")):
        if field in values and values[field].strip().isdigit():
            changes[key] = int(values[field])
    if "Number" in values and "volume" not in changes and values["Number"].strip().isdigit():
        changes["volume"] = int(values["Number"])
    for role in CREDIT_FIELDS:
        if role in values:
            changes.setdefault("credits", {})[role] = [n.strip() for n in values[role].split(",") if n.strip()]
    if "Genre" in values:
        changes["genres"] = [g.strip() for g in values["Genre"].split(",") if g.strip()]
    return changes
