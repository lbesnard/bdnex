"""``bdnex reset``: empty the library, keep what was downloaded."""

from __future__ import annotations

import argparse
import datetime
import sys

from bdnex.formatting import human_size
from bdnex.library import Library
from bdnex.ui import context
from bdnex.ui.output import add_pretend

LABELS = (("files", "files"), ("albums", "albums"), ("series", "series"), ("people", "people"),
          ("genres", "genres"), ("publishers", "publishers"), ("import_log", "import log entries"))


def run(argv: list[str], out=sys.stdout, prompter=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex reset", description=__doc__,
        epilog="Deletes the files, albums, series, credits, genres and publishers recorded in the "
               "library, with your `bdnex edit` changes and the import history. Kept: the sitemaps "
               "and every album / series page and cover already fetched, so the next import needs "
               "no network for them. Your comic files are not touched.")
    add_pretend(parser, "be deleted")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--no-backup", action="store_true",
                        help="do not copy the database to <library>.before-reset-<date> first")
    args = parser.parse_args(argv)
    settings = context.settings(doing="nothing to reset.")
    if settings is None:
        return 1

    with Library.for_settings(settings, readonly=args.pretend) as library:  # -n changes nothing: not even an upgrade of the file
        counts = library.reset(apply=False)
        cache = library.cache_summary()
        from bdnex.ui import color

        on = color.enabled(out)
        print(color.role("Would be forgotten" if args.pretend else "About to forget", "bad", on)
              + " (your collection's records in the library):", file=out)
        for key, label in LABELS:
            print(f"  {color.role(f'{counts[key]:>7}', 'bad', on) if counts[key] else f'{counts[key]:>7}'} {label}", file=out)
        kept = ", ".join(f"{n} {kind}" for kind, (n, _) in sorted(cache.items())) or "nothing"
        size = human_size(sum(size for _, size in cache.values()))
        print(color.role(f"Kept, not touched: the downloaded pages and covers ({kept}; {size}) and your comic files.", "good", on),
              file=out)
        if args.pretend:
            return 0
        if not any(counts.values()):
            print("The library is already empty.", file=out)
            return 0
        if not args.yes:
            if prompter is None:
                from bdnex.ui.prompts import new_prompter
                prompter = new_prompter()
            if not prompter.confirm("Forget these records? The downloaded pages and covers and your comic files stay."):
                print("Nothing changed.", file=out)
                return 1
        if not args.no_backup:
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = library.backup_to(f"{settings.library_path}.before-reset-{stamp}")
            print(f"{color.role('Backup of the database', 'good', on)}: {color.role(str(backup), 'path', on)}", file=out)
        library.reset()
        print(color.hints("The library is empty. `bdnex import <folder>` starts again; pages already fetched are not "
                          "downloaded twice.", on), file=out)
    return 0
