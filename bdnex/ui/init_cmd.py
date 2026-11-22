"""``bdnex init``: download (or refresh) the album list and the series index the matching and the searches work from."""

from __future__ import annotations

import argparse
import sys

from bdnex.fetchers import FetcherError
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.library import Library
from bdnex.ui import context, mirror_cmd
from bdnex.ui.output import Bar, add_verbosity, setup_logging
from bdnex.ui.prompts import new_prompter

EXPECTED_FILES = 56  # a guess for the progress bar on the very first download


def download(source, library, out=sys.stdout, bar_stream=None) -> tuple[int, int]:
    """Fetch the album list of ``source`` with a progress bar. Returns (albums before, albums now)."""
    count = getattr(source, "album_count", lambda: 0)
    before = count()
    cached = getattr(source, "sitemaps_cached", lambda: 0)()
    bar = Bar(cached or EXPECTED_FILES, f"Album list ({source.name})", stream=bar_stream, exact=False)
    try:
        source.prepare(recache=True, progress=lambda message: bar.update(message.strip().split(": ", 1)[-1]))
    finally:
        bar.finish()
    after = count()
    if after > before or not before:
        library.set_meta("sitemaps_at")  # files you skipped before get another chance (see `import`)
    library.set_meta("sitemap_albums", str(after))
    return before, after


def download_series(source, out=sys.stdout, bar_stream=None, recache: bool = False) -> tuple[int, int]:
    """Read the letter index of ``source`` (every series by name), if it has one. Returns (series before, series now)."""
    from bdnex.importer.bedetheque import letters

    if not hasattr(source, "update_series_index"):
        return 0, 0
    before = source.series_count()
    bar = Bar(len(letters.LETTERS), f"Series index ({source.name})", stream=bar_stream, exact=False)
    try:
        source.update_series_index(recache=recache, progress=lambda message: bar.update(message.strip()))
    finally:
        bar.finish()
    return before, source.series_count()


def run(argv: list[str], out=sys.stdout, bar_stream=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex init", description=__doc__,
        epilog="The matching of file names works offline from this list (about 55 files, 70 MB). Run it "
               "once before your first import, and again whenever you want to pick up new albums. `--mirror` goes further: it also "
               "downloads every album page, series page and cover the list has and the library lacks (a long run: tens of GB; "
               "Ctrl-C stops it, the next run goes on).")
    parser.add_argument("--skip-list", action="store_true",
                        help="do not download the album list again: use the one already here (with --mirror)")
    parser.add_argument("--skip-series", action="store_true",
                        help="do not read the series index (the 27 letter pages, every series by name)")
    parser.add_argument("--series-only", action="store_true",
                        help="only read the series index, not the album list")
    parser.add_argument("--mirror", action="store_true",
                        help="after the list, download every album page, series page and cover the library lacks (long: asks first)")
    mirror_cmd.add_options(parser)
    add_verbosity(parser)
    args = parser.parse_args(argv)
    mirror_cmd.check_options(args, parser)
    if args.series_only and args.skip_series:
        parser.error("--series-only and --skip-series cancel each other")
    if args.mirror and not (args.yes or args.pretend or sys.stdin.isatty()):
        parser.error("--mirror asks before it fetches, and there is no terminal to ask on: add --yes (or -n to only count)")
    setup_logging(args.verbose)
    settings = context.settings(library=False)
    if settings is None:
        return 1
    list_wanted = not (args.series_only or args.skip_list or args.pretend)  # -n only counts what a mirror would do: nothing is downloaded
    series_wanted = not (args.skip_series or args.pretend)
    with Library.for_settings(settings, readonly=args.pretend) as library:
        try:
            source = create_source(settings.source, library, settings)
        except (UnknownSource, FetcherError, TypeError) as err:
            print(f"{err}\nCheck `source` in your configuration (`bdnex config --edit`).", file=sys.stderr)
            return 1
        before = after = source.album_count() if hasattr(source, "album_count") else 0
        if list_wanted:
            try:
                before, after = download(source, library, out, bar_stream)
            except source.fetch_errors as err:
                print(f"Could not download the album list: {err}", file=sys.stderr)
                return 1
        series_before = series_after = 0
        if series_wanted:
            try:
                series_before, series_after = download_series(source, out, bar_stream, recache=True)
            except source.fetch_errors as err:
                print(f"Could not read the series index: {err}\nThe album list is kept; `bdnex init --series-only` tries again.",
                      file=sys.stderr)
                return 1
        from bdnex.ui import color

        on = color.enabled(out)
        if list_wanted:
            news = f" ({after - before:+,} since the last time)" if before else ""
            print(f"{color.role('Done', 'good', on)}: {color.role(f'{after:,}', 'count', on)} albums in the list{news}.", file=out)
        if series_after:
            more = f" ({series_after - series_before:+,} since the last time)" if series_before else ""
            print(f"{color.role('Done', 'good', on)}: {color.role(f'{series_after:,}', 'count', on)} series in the index{more}.", file=out)
        if args.mirror:
            ask = None if args.yes else (lambda question: new_prompter().confirm(question))
            return mirror_cmd.mirror_albums(args, settings, library, source, out, bar_stream, ask)
    print(color.hints("Next: `bdnex import <folder>`.", on), file=out)
    return 0
