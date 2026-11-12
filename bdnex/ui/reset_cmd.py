"""``bdnex reset``: empty the library, keep what was downloaded."""

from __future__ import annotations

import argparse
import datetime
import sys

from bdnex.config import ConfigError, load_config
from bdnex.library import Library

LABELS = (("files", "files"), ("albums", "albums"), ("series", "series"), ("people", "people"),
          ("genres", "genres"), ("publishers", "publishers"), ("import_log", "import log entries"))


def run(argv: list[str], out=sys.stdout, prompter=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex reset", description=__doc__,
        epilog="Deletes the files, albums, series, credits, genres and publishers recorded in the "
               "library, with your `bdnex edit` changes and the import history. Kept: the sitemaps "
               "and every album / series page and cover already fetched, so the next import needs "
               "no network for them. Your comic files are not touched.")
    parser.add_argument("-n", "--pretend", action="store_true", help="show what would be deleted; change nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--no-backup", action="store_true",
                        help="do not copy the database to <library>.before-reset-<date> first")
    args = parser.parse_args(argv)
    try:
        settings = load_config().settings
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    if not settings.library_path.exists():
        print(f"No library yet ({settings.library_path}): nothing to reset.", file=sys.stderr)
        return 1

    with Library.for_settings(settings) as library:
        counts = library.reset(apply=False)
        cache = library.cache_summary()
        print("Would be deleted:" if args.pretend else "About to delete:", file=out)
        for key, label in LABELS:
            print(f"  {counts[key]:>7} {label}", file=out)
        kept = ", ".join(f"{n} {kind}" for kind, (n, _) in sorted(cache.items())) or "nothing"
        megabytes = sum(size for _, size in cache.values()) / 1048576
        print(f"Kept: the downloaded data ({kept}; {megabytes:.1f} MB) and your comic files.", file=out)
        if args.pretend:
            return 0
        if not any(counts.values()):
            print("The library is already empty.", file=out)
            return 0
        if not args.yes:
            if prompter is None:
                from bdnex.ui.prompts import InquirerPrompter
                prompter = InquirerPrompter()
            if not prompter.confirm("Delete all of this from the library?"):
                print("Nothing changed.", file=out)
                return 1
        if not args.no_backup:
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = library.backup_to(f"{settings.library_path}.before-reset-{stamp}")
            print(f"Backup of the database: {backup}", file=out)
        library.reset()
        print("The library is empty. `bdnex import <folder>` starts again; pages already fetched are not "
              "downloaded twice.", file=out)
    return 0
