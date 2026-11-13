"""``bdnex duplicates``: the same book in several files."""

from __future__ import annotations

import argparse
import sys

from bdnex.config import ConfigError, load_config
from bdnex.library import Library
from bdnex.library.health import duplicates
from bdnex.ui import color
from bdnex.ui.output import progress


def _megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB"


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex duplicates", description=__doc__,
        epilog="Groups files with the same content, several files for one album (not marked as spare versions), and "
               "files whose first page looks alike. It only reports: `bdnex import` asks what to do about a duplicate "
               "when it meets one, and `bdnex trash` holds what was replaced.")
    parser.add_argument("--covers", action="store_true",
                        help="also compare the first pages of every file (reads each file once; slow on a big library)")
    args = parser.parse_args(argv)
    try:
        settings = load_config().settings
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    if not settings.library_path.exists():
        print(f"No library yet ({settings.library_path}).", file=sys.stderr)
        return 1

    on = color.enabled(out)
    with Library.for_settings(settings, readonly=not args.covers) as library:
        groups = duplicates(library, compute_covers=args.covers, progress=progress if args.covers else None)
        for group in groups:
            print(f"\n{color.paint('●', 'cyan', on)} {color.paint(group['why'], 'bold', on)}", file=out)
            for row in group["files"]:
                spare = "  (spare)" if row.get("spare") else ""
                print(f"    {row['format']:<4} {row['page_count'] or '?':>4} pages  {_megabytes(row['size']):>9}  "
                      f"{row['path']}{spare}", file=out)
    wasted = sum(sum(f["size"] for f in g["files"]) - max(f["size"] for f in g["files"]) for g in groups if g["why"] == "identical content")
    print(f"\n{len(groups)} group(s) of files that look like the same book."
          + (f" Identical copies take {_megabytes(wasted)} too much." if wasted else "")
          + ("" if args.covers else " (`--covers` also compares first pages.)"), file=out)
    return 0
