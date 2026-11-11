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
    if album.get("isbn"):
        fields["Notes"] = f"ISBN: {album['isbn']}"
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

    kind: str  # "+" only in the database, "-" only in the file, "~" differs
    field: str
    before: Optional[str]  # shortened value in the file
    after: Optional[str]  # shortened value in the database
    note: str

    def __new__(cls, kind: str, field: str, before: Optional[str], after: Optional[str]):
        before, after, note = excerpts(before, after)
        if kind == "+":
            text = f"+ {field}: {after}"
        elif kind == "-":
            text = f"- {field}: {before}"
        else:
            text = f"~ {field}: {before}  ->  {after}"
        obj = super().__new__(cls, text + (f"  {note}" if note else ""))
        obj.kind, obj.field, obj.before, obj.after, obj.note = kind, field, before, after, note
        return obj


def diff_fields(old: dict[str, str], new: dict[str, str]) -> list[Change]:
    """Differences between a file's ComicInfo (``old``) and the database's (``new``), for the fields
    bdnex manages plus fields present only in ``old`` and managed by bdnex."""
    changes = []
    for name in sorted(set(old) | set(new)):
        before, after = old.get(name), new.get(name)
        if before == after:
            continue
        if name not in new and name not in MANAGED:
            continue  # a field bdnex never writes: leave it alone, not a difference
        kind = "+" if before is None else "-" if after is None else "~"
        changes.append(Change(kind, name, before, after))
    return changes


MANAGED = {"Title", "Series", "Number", "Volume", "AlternateNumber", "Year", "Month", "Day", *CREDIT_FIELDS,
           "Summary", "Publisher", "Web", "LanguageISO", "Format", "Genre", "CommunityRating", "Notes", "PageCount"}


def merge_with_existing(existing: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """Database values win for managed fields; other fields of the file are kept."""
    merged = {k: v for k, v in existing.items() if k not in MANAGED}
    merged.update(new)
    return merged
