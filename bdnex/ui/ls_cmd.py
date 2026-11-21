"""``bdnex ls``: query the library."""

from __future__ import annotations

import argparse
import json
import string
import sys
from typing import Any

from bdnex.library import Library
from bdnex.library.query import ALL_FIELDS, QueryError
from bdnex.ui import color, context, describe

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
        prog="bdnex ls", description="List albums (or files) of the library. Terms are ANDed; a lone `,` means or; "
                                     "`year-` or `title+` sorts.",
        epilog="fields: " + ", ".join(ALL_FIELDS))
    parser.add_argument("query", nargs="*", help="terms such as  series:nains  author:uderzo  year:2010..2015  ^genre:humour")
    parser.add_argument("--fields", action="store_true", help="list the fields a query can use, with their kind and what they mean (nothing else)")
    parser.add_argument("--files", action="store_true", help="list files instead of albums")
    parser.add_argument("--format", help="output template, e.g. '$series T$vol $title'")
    parser.add_argument("--limit", type=int, metavar="N", help="at most N results (after sorting: `year-` puts the newest first)")
    parser.add_argument("-c", "--count", action="store_true", help="print only the number of results")
    parser.add_argument("-l", "--long", action="store_true",
                        help="show everything known about each result (with --json: the complete records)")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    parser.add_argument("--sitemap", action="store_true",
                        help="search the whole album list (the sitemap: every album of the site, not only yours) instead of the library; "
                             "the words are the album's name (case and accents do not matter, a typo is forgiven); --limit defaults to 20")
    parser.add_argument("--for", dest="for_files", action="append", default=[], metavar="PATH",
                        help="with --sitemap: the albums this file could be, searched by its name, its folder names and its volume, "
                             "the right volume first (repeat for several files)")
    parser.add_argument("--unmatched", action="store_true",
                        help="list files that have no album (skipped, failed or not linked); terms filter the path")
    parser.add_argument("--missing", action="store_true",
                        help="list registered files that are no longer on disk (moved or deleted outside bdnex)")
    args = parser.parse_args(argv)
    if args.fields:
        from bdnex.ui import stats_cmd

        return stats_cmd.print_fields(out)

    settings = context.settings()
    if settings is None:
        return 1

    if args.for_files and not args.sitemap:
        args.sitemap = True
    if args.sitemap:
        if not (args.query or args.for_files):
            print("Give words to search for (`bdnex ls --sitemap batman dark victory`) or a file (`--for PATH`).", file=sys.stderr)
            return 2
        from bdnex.ui import sitemap_search

        return sitemap_search.run(settings, args.query, args.for_files, args.limit or 20, as_json=args.json, count=args.count, out=out)

    if args.unmatched or args.missing:
        return _file_problems(settings, args, out)

    with Library.for_settings(settings) as library:
        try:
            rows = library.query(args.query, files=args.files, limit=args.limit)
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
        on = color.enabled(out) and not args.format  # your own --format is printed as you wrote it
        for row in rows:
            print(template.safe_substitute(tint_row(decorate(row), on)), file=out)
    return 0


def tint_row(row: dict, on: bool) -> dict:
    """The default format on a terminal: the name of the album is what the eye lands on, what is missing stands out,
    the rest (authors, year, counts, paths) is quiet. A row of values that are not text is left as it is."""
    if not on:
        return row
    row = dict(row)
    row["label"] = color.role(str(row["label"]), "name", on)
    row["flag"] = color.role(str(row["flag"]), "warn", on)
    row["files_label"] = color.role(str(row["files_label"]), "note", on)
    row["path"] = color.role(str(row.get("path", "")), "path", on)
    row["authors"] = color.role(str(row.get("authors", "")), "note", on)
    row["year"] = color.role(str(row.get("year", "")), "note", on)
    return row


def _details(library, rows, args) -> dict:
    """Complete records (for --long): albums with credits, genres and files; or files with their album."""
    records, text = [], []
    for row in rows:
        album_id = row["id"]
        album = library.get_album(album_id) if album_id else None
        if args.files:
            label = describe.label(album) if album else "(not imported)"
            marks = library.attrs_of("file", row["file_id"])
            text.append(describe.file_block(row, label) + _marks_line(marks))
            records.append({**row, "album_record": album, "marks": marks})
        else:
            files = library.files_for_album(album_id)
            marks = library.attrs_of("album", album_id)
            text.append(describe.long(album, files) + _marks_line(marks))
            records.append({**album, "file_records": files, "marks": marks})
    return records if args.json else {"text": text, "records": records}


def _marks_line(marks: dict[str, str]) -> str:
    """The marks of `bdnex mark`, one line (nothing when there are none)."""
    return "\n  marks: " + ", ".join(f"{k}={v}" if v else k for k, v in marks.items()) if marks else ""


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
        on = color.enabled(out) and not args.format
        for row in rows:
            shown = {k: ("" if v is None else v) for k, v in row.items()}
            if on:  # what happened to the file, how good the best match was, and the path (quiet)
                if "outcome" in shown:
                    kind = {"failed": "failed", "skipped": "skipped"}.get(str(shown["outcome"]), "note")
                    shown["outcome"] = color.verdict(f"{shown['outcome']:<8}", kind, on)
                if row.get("score") is not None:
                    shown["best"] = color.paint_score(shown["best"], float(row["score"]), on)
                shown["path"] = color.role(str(shown.get("path", "")), "name", on) if args.missing else str(shown.get("path", ""))
            print(template.safe_substitute(shown), file=out)
    return 0
