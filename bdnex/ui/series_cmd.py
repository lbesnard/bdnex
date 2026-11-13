"""``bdnex series``: what you have of each series, and what is missing."""

from __future__ import annotations

import argparse
import json
import sys

from bdnex.library import Library
from bdnex.ui import context


def label(album: dict) -> str:
    number = f"T{album['volume']:02d}" if album.get("volume") is not None else "special"
    return f"{number}  {album['title']}".rstrip()


def details(extra: dict) -> list[str]:
    """What the series page said, one line each: the normalised values it has."""
    lines = []
    for key, name in (("genres", "genres"), ("status", "status"), ("origin", "origin"), ("language", "language")):
        value = extra.get(key)
        if value:
            lines.append(f"{name}: {', '.join(value) if isinstance(value, list) else value}")
    if extra.get("rating"):
        votes = f" ({extra['rating_count']} votes)" if extra.get("rating_count") else ""
        lines.append(f"rating: {extra['rating']}{votes}")
    return lines


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex series", description=__doc__,
        epilog="The albums a series has come from its page, read when one of its albums is fetched "
               "(`bdnex import --fetch`). Until then only what you own is known.")
    parser.add_argument("name", nargs="*", help="words of the series name (case and accents ignored)")
    parser.add_argument("--missing", action="store_true", help="only the series that lack albums")
    parser.add_argument("-l", "--long", action="store_true", help="list the missing albums, with their address")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    settings = context.settings()
    if settings is None:
        return 1

    with Library.for_settings(settings) as library:
        rows = library.series_overview(args.name)
    if args.missing:
        rows = [r for r in rows if r["missing"]]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2), file=out)
        return 0
    for row in rows:
        if row["known"] is None:
            print(f"{row['name']}  {row['owned']} album(s)  (the series' own album list is not known yet)", file=out)
            if args.long:
                for line in details(row["extra"]):
                    print(f"    {line}", file=out)
            continue
        missing = row["missing"]
        print(f"{row['name']}  {row['owned']} of {row['known']} albums"
              + (f"  ·  missing {len(missing)}" if missing else "  ·  complete"), file=out)
        if args.long:
            for line in details(row["extra"]):
                print(f"    {line}", file=out)
            for album in missing:
                print(f"    {label(album)}  {album['url']}", file=out)
    if not rows:
        print("No series." if not args.name else "No series with that name.", file=out)
    return 0
