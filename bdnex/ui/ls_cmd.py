"""``bdnex ls``: query the library."""

from __future__ import annotations

import argparse
import json
import string
import sys
from typing import Any

from bdnex.library import Library
from bdnex.library.query import ALL_FIELDS, QueryError
from bdnex.ui import context, describe

DEFAULT_ALBUM_FORMAT = "$label$flag  ($authors) [$year]  $files_label"
DEFAULT_FILE_FORMAT = "$path  $label"


def decorate(row: dict[str, Any]) -> dict[str, Any]:
    """Add the derived fields usable in ``--format`` (``$label``, ``$vol``, ...)."""
    row = {k: ("" if v is None else v) for k, v in row.items()}
    volume, title, series = row.get("volume"), row.get("title") or "", row.get("series") or ""
    row["vol"] = f"{int(volume):02d}" if volume != "" else (row.get("alternate_number") or "")
    if series and row["vol"]:
        row["label"] = f"{series} - T{row['vol']} - {title}" if title and title != series else f"{series} - T{row['vol']}"
    else:
        row["label"] = title or series
    if not row.get("id") and "file_id" in row:
        row["label"] = "(not imported)"
    row["flag"] = " [not fetched]" if row.get("partial") else ""
    n = row.get("files", 0)
    row["files_label"] = f"{n} file{'s' if n != 1 else ''}" if n != "" else ""
    return row


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex ls", description="List albums (or files) of the library. Terms are ANDed.",
        epilog="fields: " + ", ".join(ALL_FIELDS))
    parser.add_argument("query", nargs="*", help="terms such as  series:nains  author:uderzo  year:2010..2015  ^genre:humour")
    parser.add_argument("--files", action="store_true", help="list files instead of albums")
    parser.add_argument("--format", help="output template, e.g. '$series T$vol $title'")
    parser.add_argument("-c", "--count", action="store_true", help="print only the number of results")
    parser.add_argument("-l", "--long", action="store_true",
                        help="show everything known about each result (with --json: the complete records)")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    parser.add_argument("--unmatched", action="store_true",
                        help="list files that have no album (skipped, failed or not linked); terms filter the path")
    parser.add_argument("--missing", action="store_true",
                        help="list registered files that are no longer on disk (moved or deleted outside bdnex)")
    args = parser.parse_args(argv)

    settings = context.settings()
    if settings is None:
        return 1

    if args.unmatched or args.missing:
        return _file_problems(settings, args, out)

    with Library.for_settings(settings) as library:
        try:
            rows = library.query(args.query, files=args.files)
        except QueryError as err:
            return context.bad_query(err)
        details = _details(library, rows, args) if args.long else None

    if args.count:
        print(len(rows), file=out)
    elif args.json:
        print(json.dumps(details if details is not None else rows, ensure_ascii=False, indent=2), file=out)
    elif args.long:
        print("\n\n".join(details["text"]), file=out)
    else:
        template = string.Template(args.format or (DEFAULT_FILE_FORMAT if args.files else DEFAULT_ALBUM_FORMAT))
        for row in rows:
            print(template.safe_substitute(decorate(row)), file=out)
    return 0


def _details(library, rows, args) -> dict:
    """Complete records (for --long): albums with credits, genres and files; or files with their album."""
    records, text = [], []
    for row in rows:
        album_id = row["id"]
        album = library.get_album(album_id) if album_id else None
        if args.files:
            label = describe.label(album) if album else "(not imported)"
            text.append(describe.file_block(row, label))
            records.append({**row, "album_record": album})
        else:
            files = library.files_for_album(album_id)
            text.append(describe.long(album, files))
            records.append({**album, "file_records": files})
    return records if args.json else {"text": text, "records": records}


def _file_problems(settings, args, out) -> int:
    """``--unmatched`` and ``--missing``: files, not albums."""
    from bdnex.library.query import fold

    with Library.for_settings(settings) as library:
        if args.unmatched:
            rows = library.unmatched(args.query)
            default = "$outcome  $best  $path"
        else:
            wanted = [fold(t) for t in args.query]
            rows = [r for r in library.missing_files() if all(t in fold(r["path"]) for t in wanted)]
            default = "$path"
    for row in rows:
        score = row.get("score")
        row["best"] = f"{score:5.1f}" if score is not None else "    -"
    if args.count:
        print(len(rows), file=out)
    elif args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str), file=out)
    else:
        template = string.Template(args.format or default)
        for row in rows:
            print(template.safe_substitute({k: ("" if v is None else v) for k, v in row.items()}), file=out)
    return 0
