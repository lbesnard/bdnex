"""``bdnex relink``: files were moved or renamed outside bdnex; find them again by their content."""

from __future__ import annotations

import argparse
import os
import sys

from bdnex.library import Library
from bdnex.ui import color, context, results
from bdnex.ui.output import add_pretend, add_workers


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex relink", description=__doc__,
        epilog="A file the library knows but that is no longer where it was (`bdnex ls --missing`) is looked for "
               "in the folders you give (default: your library folder), by size and checksum. A single copy is "
               "linked again, with its album and history. Nothing on disk is touched.")
    parser.add_argument("folders", nargs="*", help="where to look (default: your collection, `collection_path` in the configuration)")
    add_pretend(parser, "be linked")
    add_workers(parser, "files read")
    args = parser.parse_args(argv)
    settings = context.settings(collection=not args.folders)
    if settings is None:
        return 1
    folders = args.folders or [str(settings.require_collection())]

    with Library.for_settings(settings, readonly=args.pretend) as library:
        from bdnex.importer.pipeline import resolve_workers

        workers = resolve_workers(settings.import_.workers if args.workers is None else args.workers)
        result = library.relink(folders, apply=not args.pretend, workers=workers)
    on = color.enabled(out)
    for old, new in result["found"]:
        print(results.block("would link" if args.pretend else "linked", os.path.basename(old),
                            [f"was   {color.paint(old, 'red', on)}", f"now   {color.paint(new, 'green', on)}"], on,
                            "would do" if args.pretend else "found"), file=out)
    for old, copies in result["ambiguous"]:
        print(results.block("several copies", os.path.basename(old), [*copies, color.paint("left alone", "dim", on)], on,
                            "ambiguous"), file=out)
    for old in result["lost"]:
        print(results.block("not found", os.path.basename(old), [color.paint(old, "dim", on)], on, "lost"), file=out)
    verb = "would be linked again" if args.pretend else "linked again"
    print(results.footer([(f"{len(result['found'])} {verb}", "found" if result["found"] else "unchanged"),
                          (f"{len(result['ambiguous'])} with several copies", "ambiguous" if result["ambiguous"] else "unchanged"),
                          (f"{len(result['lost'])} not found", "lost" if result["lost"] else "unchanged")], on), file=out)
    return 0
