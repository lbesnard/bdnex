"""``bdnex duplicates``: the same book in several files."""

from __future__ import annotations

import argparse
import sys

from bdnex.formatting import human_size
from bdnex.library import Library
from bdnex.library.health import duplicates
from bdnex.ui import color, context, results
from bdnex.ui.output import add_workers, progress


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex duplicates", description=__doc__,
        epilog="Groups files with the same content, several files for one album (not marked as spare versions), and "
               "files whose first page looks alike. It only reports: `bdnex import` asks what to do about a duplicate "
               "when it meets one, and `bdnex trash` holds what was replaced.")
    parser.add_argument("--covers", action="store_true",
                        help="also compare the first pages of every file (reads each file once; slow on a big library)")
    add_workers(parser, "files read with --covers")
    args = parser.parse_args(argv)
    settings = context.settings()
    if settings is None:
        return 1

    on = color.enabled(out)
    with Library.for_settings(settings, readonly=not args.covers) as library:
        from bdnex.importer.pipeline import resolve_workers

        workers = resolve_workers(settings.import_.workers if args.workers is None else args.workers)
        groups = duplicates(library, compute_covers=args.covers, progress=progress if args.covers else None, workers=workers)
        for group in groups:
            lines = [f"{row['format']:<4} {row['page_count'] or '?':>4} pages  {human_size(row['size']):>9}  "
                     f"{row['path']}{'  (spare)' if row.get('spare') else ''}" for row in group["files"]]
            print(results.block("", group["why"], lines, on, "group"), file=out)
    wasted = sum(sum(f["size"] for f in g["files"]) - max(f["size"] for f in g["files"]) for g in groups if g["why"] == "identical content")
    print(f"\n{len(groups)} group(s) of files that look like the same book."
          + (f" Identical copies take {human_size(wasted)} too much." if wasted else "")
          + ("" if args.covers else " (`--covers` also compares first pages.)"), file=out)
    return 0
