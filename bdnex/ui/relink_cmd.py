"""``bdnex relink``: files were moved or renamed outside bdnex; find them again by their content."""

from __future__ import annotations

import argparse
import sys

from bdnex.config import ConfigError, load_config
from bdnex.library import Library


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex relink", description=__doc__,
        epilog="A file the library knows but that is no longer where it was (`bdnex ls --missing`) is looked for "
               "in the folders you give (default: your library folder), by size and checksum. A single copy is "
               "linked again, with its album and history. Nothing on disk is touched.")
    parser.add_argument("folders", nargs="*", help="where to look (default: `directory` in the configuration)")
    parser.add_argument("-n", "--pretend", action="store_true", help="show what would be linked; change nothing")
    args = parser.parse_args(argv)
    try:
        settings = load_config().settings
        folders = args.folders or [str(settings.require_library_directory())]
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    if not settings.library_path.exists():
        print(f"No library yet ({settings.library_path}).", file=sys.stderr)
        return 1

    with Library.for_settings(settings, readonly=args.pretend) as library:
        result = library.relink(folders, apply=not args.pretend)
    for old, new in result["found"]:
        print(f"{old}\n    -> {new}", file=out)
    for old, copies in result["ambiguous"]:
        print(f"{old}\n    several copies, left alone: " + "; ".join(copies), file=out)
    for old in result["lost"]:
        print(f"{old}\n    not found", file=out)
    verb = "would be linked again" if args.pretend else "linked again"
    print(f"\n{len(result['found'])} {verb}, {len(result['ambiguous'])} with several copies, "
          f"{len(result['lost'])} not found.", file=out)
    return 0
