"""``bdnex relocate``: your comics moved; tell the library (the whole folder: OLD NEW), or find the moved files (--find)."""

from __future__ import annotations

import argparse
import os
import sys

from bdnex.library import Library
from bdnex.ui import context
from bdnex.ui.output import add_pretend, add_workers


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex relocate", description=__doc__,
        epilog="Files under `collection_path` are stored relative to it: after moving the whole collection, just "
               "change `collection_path`. A folder listed in `import.sources` is one record in the library: "
               "`relocate OLD NEW` with its old path updates that record and its files follow (then change the path in "
               "`import.sources` too). For other files stored with their full path, `relocate` rewrites the stored "
               "paths. It touches nothing on disk. `bdnex ls --missing` lists what is not found. To find individual files that "
               "were moved or renamed, by their content, use `--find` (`bdnex relocate --find FOLDER...`).")
    parser.add_argument("folders", nargs="*", metavar="OLD NEW | FOLDER",
                        help="the folder the files used to be in, then the folder they are in now; with --find: where to "
                             "look (default: your collection, `collection_path` in the configuration)")
    parser.add_argument("--find", action="store_true",
                        help="instead: look for the files the library knows but that are no longer where they were "
                             "(`bdnex ls --missing`) in the folders given, by size and checksum, and link a single copy "
                             "again, with its album and history. Nothing on disk is touched")
    add_workers(parser, "files read", note="; with --find")
    add_pretend(parser, "change")
    args = parser.parse_args(argv)
    if args.find:
        from bdnex.ui import relink_cmd

        return relink_cmd.run([*args.folders, *(["-n"] if args.pretend else []),
                               *(["--workers", str(args.workers)] if args.workers is not None else [])], out)
    if len(args.folders) != 2:
        parser.error("give the folder the files used to be in and the folder they are in now (or --find to look for them)")
    args.old, args.new = args.folders
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
    from bdnex.ui import color

    on = color.enabled(out)
    print(f"{color.role(verb, 'good', on)} {result['files']} file path(s) and {result['log']} import log "
          f"entr{'y' if result['log'] == 1 else 'ies'}.", file=out)
    everything = present == result["files"]
    print(color.role(f"{present} of {result['files']} file(s) exist at the new location.", "good" if everything else "warn", on),
          file=out)
    if not everything:
        print(color.hints("The others will show in `bdnex ls --missing`.", on), file=out)
    return 0
