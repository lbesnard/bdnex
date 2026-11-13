"""``bdnex relocate``: your comics moved to another folder; tell the library."""

from __future__ import annotations

import argparse
import os
import sys

from bdnex.library import Library
from bdnex.ui import context
from bdnex.ui.output import add_pretend


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex relocate", description=__doc__,
        epilog="Files under `collection_path` are stored relative to it: after moving the whole collection, just "
               "change `collection_path`. A folder listed in `import.sources` is one record in the library: "
               "`relocate OLD NEW` with its old path updates that record and its files follow (then change the path in "
               "`import.sources` too). For other files stored with their full path, `relocate` rewrites the stored "
               "paths. It touches nothing on disk. `bdnex ls --missing` lists what is not found.")
    parser.add_argument("old", help="the folder the files used to be in")
    parser.add_argument("new", help="the folder they are in now")
    add_pretend(parser, "change")
    args = parser.parse_args(argv)
    settings = context.settings()
    if settings is None:
        return 1

    with Library.for_settings(settings, sync_sources=False) as library:  # the sources are about to change
        result = library.relocate(args.old, args.new, apply=not args.pretend)
    if result.get("source"):
        verb = "Would point" if args.pretend else "Pointed"
        print(f"{verb} the source {result['source']!r} at {os.path.abspath(os.path.expanduser(args.new))}: its "
              f"{result['files']} file(s) follow, nothing else is rewritten.", file=out)
        print("Make `import.sources` in your configuration say the same (`bdnex config --edit`).", file=out)
        return 0
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
