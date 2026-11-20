"""``bdnex reparse``: rebuild album records from the pages already stored in the library, offline."""

from __future__ import annotations

import argparse
import sys

from bdnex.importer import pipeline
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui import color, context, describe, results
from bdnex.ui.output import add_pretend, add_verbosity, add_workers, progress, setup_logging

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


ONESHOT_CHUNK = 25  # albums without a series are read this many to a task


def read_groups(settings, offline, groups: list[list[str]], workers: int, tick=lambda done: None) -> dict:
    """The fresh parse of each album page, ``{address: (AlbumInfo or None, error or "")}``, read in ``workers`` processes.

    A task is the albums of one series, so that its series page is read once. Each process has a library connection of its
    own (read-only) and nothing is written here: the caller writes, in its own process, once everything is read."""
    from bdnex import parallel

    state: dict = {}

    def read(urls: list[str]) -> list:
        if "source" not in state:  # in the worker: its own connection, opened after the fork
            library = Library.for_settings(settings, readonly=True)
            state["source"] = create_source(settings.source, library, offline)
        found = []
        for url in urls:
            try:
                found.append((url, state["source"].album_info(url), ""))
            except Exception as err:  # a page that no longer parses must not stop the others
                found.append((url, None, str(err) or err.__class__.__name__))
        return found

    parsed: dict = {}
    done = 0
    for urls, result in parallel.map_ordered(read, groups, workers):
        if isinstance(result, Exception):  # the whole task failed (a worker died): say so for each of its albums
            result = [(url, None, str(result) or result.__class__.__name__) for url in urls]
        for url, info, error in result:
            parsed[url] = (info, error)
        done += len(urls)
        tick(done)
    return parsed


def group_rows(rows: list, library) -> tuple[list, list]:
    """The albums to read, in the order of ``rows``, and their addresses grouped by series (one task each)."""
    by_series: dict = {}
    for number, row in enumerate(rows):
        by_series.setdefault(row.get("series") or f"\0{number // ONESHOT_CHUNK}", []).append(row)
    ordered = [row for group in by_series.values() for row in group]
    return ordered, [[row["url"] for row in group if row.get("url")] for group in by_series.values()]


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex reparse",
        description="Read the album pages stored in the library again and update the albums from them, "
                    "without any network access. Use it after bdnex learned to read more of a page. "
                    "Values already in the library are never erased; run `bdnex write` afterwards to put "
                    "them into the files.")
    parser.add_argument("query", nargs="*", help="albums to update, same terms as `bdnex ls` (default: all)")
    add_pretend(parser, "change")
    add_workers(parser, "album pages read", "the CPU cores less one")
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
        rows, groups = group_rows(rows, library)
        todo = [row for row in rows if row.get("url") and library.cache_has(source.name, "album", row["url"])]
        # not `import.workers`: that one is for reading big comic files (people keep it low for the memory); parsing small pages wants every core
        workers = pipeline.resolve_workers(args.workers or 0)
        progress(f"Reading {len(todo)} stored page(s), {workers} at a time...")
        parsed = read_groups(settings, offline, [[u for u in group if library.cache_has(source.name, "album", u)] for group in groups],
                             workers, lambda done: progress(f"  read {done}/{len(todo)}") if done % 500 < 25 else None)
        for number, row in enumerate(rows, start=1):
            album = library.get_album(row["id"])
            if album["source"] != source.name or not album.get("url") or album["url"] not in parsed:
                missing += 1
                print(results.head("no stored page", describe.label(album), on), file=out)
                continue
            info, error = parsed[album["url"]]
            try:
                if info is None:
                    raise RuntimeError(error)
                changes = library.preview_merge(info)
            except Exception as err:
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
        if updated and not args.pretend:
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
