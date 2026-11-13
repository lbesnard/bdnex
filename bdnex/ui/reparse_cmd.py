"""``bdnex reparse``: rebuild album records from the pages already stored in the library, offline."""

from __future__ import annotations

import argparse
import sys

from bdnex.importer.registry import UnknownSource, create_source
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui import color, context, describe, results
from bdnex.ui.output import add_pretend, add_verbosity, progress, setup_logging

OFFLINE = "bdnex.importer.registry:offline_fetch"
DETAIL_STYLE = {"new details": "green", "updated details": "yellow", "dropped details": "red"}


def format_change(line: str, on: bool) -> str:
    """``title: 'A' -> 'B'`` as ``title  A → B`` (old red, new green); ``new details: X`` in the colour of its kind."""
    field, _, rest = line.partition(": ")
    if field in DETAIL_STYLE:
        return f"{color.paint(field, 'dim', on)}  {color.paint(rest, DETAIL_STYLE[field], on)}"
    old, arrow, new = rest.partition(" -> ")
    if not arrow:
        return color.paint(line, "dim", on)
    return f"{color.paint(f'{field:<10}', 'cyan', on)} {color.paint(old, 'red', on)} {color.paint('→', 'dim', on)} {color.paint(new, 'green', on)}"


def format_album(label: str, changes: list[str], pretend: bool, number: int, total: int, on: bool) -> str:
    """One album's block: a blank line, what happens to it, then one line per change."""
    return results.block("would update" if pretend else "updated", label, [format_change(c, on) for c in changes], on,
                         "would update" if pretend else "updated", f"[{number}/{total}]")


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex reparse",
        description="Read the album pages stored in the library again and update the albums from them, "
                    "without any network access. Use it after bdnex learned to read more of a page. "
                    "Values already in the library are never erased; run `bdnex write` afterwards to put "
                    "them into the files.")
    parser.add_argument("query", nargs="*", help="albums to update, same terms as `bdnex ls` (default: all)")
    add_pretend(parser, "change")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    settings = context.settings()
    if settings is None:
        return 1
    # the source is built with a fetcher that can not reach the network, whatever the configuration says,
    # and without the politeness delays (nothing is requested, so there is nothing to be polite about)
    offline = settings.model_copy(update={"bedetheque": settings.bedetheque.model_copy(
        update={"fetcher": OFFLINE, "delay": (0, 0), "sitemap_delay": (0, 0)})})

    on = color.enabled(out)
    updated = unchanged = missing = failed = 0
    with Library.for_settings(settings) as library:
        try:
            source = create_source(settings.source, library, offline)
            rows = library.query(args.query)
        except (UnknownSource, TypeError) as err:
            print(err, file=sys.stderr)
            return 1
        except QueryError as err:
            return context.bad_query(err)
        for number, row in enumerate(rows, start=1):
            album = library.get_album(row["id"])
            progress(f"[{number}/{len(rows)}] {describe.label(album)}")
            if album["source"] != source.name or not album.get("url") \
                    or library.cache_get(source.name, "album", album["url"]) is None:
                missing += 1
                print(results.head("no stored page", describe.label(album), on), file=out)
                continue
            try:
                info = source.album_info(album["url"])
                changes = library.preview_merge(info)
            except Exception as err:  # a page that no longer parses must not stop the others
                failed += 1
                print(results.block("failed", describe.label(album), [str(err)], on), file=out)
                continue
            if not changes:
                unchanged += 1
                continue
            updated += 1
            print(format_album(describe.label(album), changes, args.pretend, number, len(rows), on), file=out)
            if not args.pretend:
                library.merge_album(info)
                library.prune_orphan_series()
    verb = "would be updated" if args.pretend else "updated"
    print(results.footer([(f"{updated} {verb}", "updated" if not args.pretend else "pretend"),
                          (f"{unchanged} unchanged", "unchanged"),
                          (f"{missing} without a stored page", "no stored page" if missing else "unchanged"),
                          *([(f"{failed} failed", "failed")] if failed else [])], on,
                         f"{len(rows)} album(s): "), file=out)
    if args.pretend and updated:
        print("Preview only: nothing was changed. Run the same command without -n to do it.", file=out)
    if updated and not args.pretend:
        print("Run `bdnex write` to put the new details into the files.", file=out)
    return 1 if failed else 0
