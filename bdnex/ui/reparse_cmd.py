"""``bdnex reparse``: rebuild album records from the pages already stored in the library, offline."""

from __future__ import annotations

import argparse
import sys

from bdnex.config import ConfigError, load_config
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui import describe
from bdnex.ui.output import add_verbosity, progress, setup_logging

OFFLINE = "bdnex.importer.registry:offline_fetch"


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex reparse",
        description="Read the album pages stored in the library again and update the albums from them, "
                    "without any network access. Use it after bdnex learned to read more of a page. "
                    "Values already in the library are never erased; run `bdnex write` afterwards to put "
                    "them into the files.")
    parser.add_argument("query", nargs="*", help="albums to update, same terms as `bdnex ls` (default: all)")
    parser.add_argument("-n", "--pretend", action="store_true", help="show what would change; change nothing")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        settings = load_config().settings
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    if not settings.library_path.exists():
        print(f"No library yet ({settings.library_path}): run `bdnex import <folder>` first.", file=sys.stderr)
        return 1
    # the source is built with a fetcher that can not reach the network, whatever the configuration says,
    # and without the politeness delays (nothing is requested, so there is nothing to be polite about)
    offline = settings.model_copy(update={"bedetheque": settings.bedetheque.model_copy(
        update={"fetcher": OFFLINE, "delay": (0, 0), "sitemap_delay": (0, 0)})})

    updated = unchanged = missing = failed = 0
    with Library.for_settings(settings) as library:
        try:
            source = create_source(settings.source, library, offline)
            rows = library.query(args.query)
        except (UnknownSource, TypeError) as err:
            print(err, file=sys.stderr)
            return 1
        except QueryError as err:
            print(f"bad query: {err}", file=sys.stderr)
            return 2
        for number, row in enumerate(rows, start=1):
            album = library.get_album(row["id"])
            progress(f"[{number}/{len(rows)}] {describe.label(album)}")
            if album["source"] != source.name or not album.get("url") \
                    or library.cache_get(source.name, "album", album["url"]) is None:
                missing += 1
                print(f"no stored page   {describe.label(album)}", file=out)
                continue
            try:
                info = source.album_info(album["url"])
                changes = library.preview_merge(info)
            except Exception as err:  # a page that no longer parses must not stop the others
                failed += 1
                print(f"failed           {describe.label(album)}: {err}", file=out)
                continue
            if not changes:
                unchanged += 1
                continue
            updated += 1
            print(f"{'would update' if args.pretend else 'updated':<16} {describe.label(album)}", file=out)
            for line in changes:
                print(f"    {line}", file=out)
            if not args.pretend:
                library.merge_album(info)
                library.prune_orphan_series()
    verb = "would be updated" if args.pretend else "updated"
    print(f"\n{len(rows)} album(s): {updated} {verb}, {unchanged} unchanged, {missing} without a stored page"
          + (f", {failed} failed" if failed else ""), file=out)
    if updated and not args.pretend:
        print("Run `bdnex write` to put the new details into the files.", file=out)
    return 1 if failed else 0
