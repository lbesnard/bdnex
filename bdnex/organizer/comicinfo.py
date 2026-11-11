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


def diff_fields(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """Human-readable differences, for the fields bdnex manages (``new``'s keys)
    plus fields present only in ``old`` and managed by bdnex."""
    lines = []
    for name in sorted(set(old) | set(new)):
        before, after = old.get(name), new.get(name)
        if before == after:
            continue
        if name not in new and name not in MANAGED:
            continue  # a field bdnex never writes: leave it alone, not a difference
        if before is None:
            lines.append(f"+ {name}: {after}")
        elif after is None:
            lines.append(f"- {name}: {before}")
        else:
            lines.append(f"~ {name}: {before}  ->  {after}")
    return lines


MANAGED = {"Title", "Series", "Number", "Volume", "AlternateNumber", "Year", "Month", "Day", *CREDIT_FIELDS,
           "Summary", "Publisher", "Web", "LanguageISO", "Format", "Genre", "CommunityRating", "Notes", "PageCount"}


def merge_with_existing(existing: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """Database values win for managed fields; other fields of the file are kept."""
    merged = {k: v for k, v in existing.items() if k not in MANAGED}
    merged.update(new)
    return merged
