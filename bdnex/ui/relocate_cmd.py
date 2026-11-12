"""``bdnex relocate``: your comics moved to another folder; tell the library."""

from __future__ import annotations

import argparse
import os
import sys

from bdnex.config import ConfigError, load_config
from bdnex.library import Library


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex relocate", description=__doc__,
        epilog="Paths under `directory` are stored relative to it: after moving the whole collection, just "
               "change `directory`. `relocate` is for files stored with their full path (outside "
               "`directory`): it rewrites the stored paths and touches nothing on disk. "
               "`bdnex ls --missing` lists what is not found.")
    parser.add_argument("old", help="the folder the files used to be in")
    parser.add_argument("new", help="the folder they are in now")
    parser.add_argument("-n", "--pretend", action="store_true", help="show what would change; change nothing")
    args = parser.parse_args(argv)
    try:
        settings = load_config().settings
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    if not settings.library_path.exists():
        print(f"No library yet ({settings.library_path}).", file=sys.stderr)
        return 1

    with Library.for_settings(settings) as library:
        result = library.relocate(args.old, args.new, apply=not args.pretend)
    if result["collisions"]:
        print("Nothing changed: these paths are already registered:", file=sys.stderr)
        for path in result["collisions"][:10]:
            print(f"  {path}", file=sys.stderr)
        return 1
    if not result["files"] and not result["log"]:
        print(f"No stored path starts with {os.path.abspath(os.path.expanduser(args.old))}.", file=sys.stderr)
        return 1
    present = sum(1 for _, new in result["moved"] if os.path.exists(new))
    verb = "Would rewrite" if args.pretend else "Rewrote"
    print(f"{verb} {result['files']} file path(s) and {result['log']} import log entr{'y' if result['log'] == 1 else 'ies'}.",
          file=out)
    print(f"{present} of {result['files']} file(s) exist at the new location.", file=out)
    if present < result["files"]:
        print("The others will show in `bdnex ls --missing`.", file=out)
    return 0
