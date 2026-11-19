"""``bdnex mirror``: keep the whole catalogue (every album page, series page and cover) in the library."""

from __future__ import annotations

import argparse
import sys

from bdnex.formatting import human_size
from bdnex.importer import mirror
from bdnex.importer.bedetheque.fetchers import FetcherError
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.library import Library
from bdnex.ui import context
from bdnex.ui.output import Bar, add_pretend, add_verbosity, add_workers, banner, setup_logging

PAGE_BYTES = 30_000  # an album page once compressed, about (the estimate of the preview)
COVER_BYTES = 100_000  # a cover image, about


def run(argv: list[str], out=sys.stdout, bar_stream=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex mirror",
        description="Download the whole Bédéthèque catalogue into the library: every album page, its series page and its cover, "
                    "for the albums the album list (`bdnex init`) has and the library does not.",
        epilog="The library is the state: stop at any time (Ctrl-C) and run it again, it goes on with what is left. Pages go "
               "through the same endpoints and the same polite delay as an import (`bedetheque.fetcher_endpoint`, `parallel`).")
    parser.add_argument("--limit", type=int, default=0, metavar="N", help="fetch at most N albums this time (0: all that are left)")
    parser.add_argument("--no-covers", action="store_true", help="the pages only: do not download the covers")
    parser.add_argument("--covers-only", action="store_true",
                        help="only the covers of the albums the library already holds without one (no page is fetched)")
    add_workers(parser, "album pages fetched", "bedetheque.parallel")
    add_pretend(parser, "be fetched: how many albums and covers are missing, and about how much room they take")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    if args.no_covers and args.covers_only:
        parser.error("--covers-only and --no-covers cancel each other")
    setup_logging(args.verbose)
    settings = context.settings(library=False)
    if settings is None:
        return 1
    from bdnex.ui import color

    on = color.enabled(out)
    with Library.for_settings(settings, readonly=args.pretend) as library:
        try:
            source = create_source(settings.source, library, settings)
        except (UnknownSource, FetcherError, TypeError) as err:
            print(f"{err}\nCheck `source` in your configuration (`bdnex config --edit`).", file=sys.stderr)
            return 1
        if not source.is_ready():
            print("There is no album list yet: `bdnex init` downloads it first.", file=sys.stderr)
            return 1
        found = mirror.plan(source, library)
        pages = [] if args.covers_only else found.to_fetch
        shown = lambda n: color.role(f"{n:,}", "count", on)  # noqa: E731
        print(f"{color.role('Album list', 'title', on)}: {shown(found.in_list)} albums; in the library with their page read: "
              f"{shown(found.complete)}; only matched: {shown(len(found.unread))}; not in the library: {shown(len(found.missing))}", file=out)
        covers_wanted = [] if args.no_covers else found.no_cover
        cover_count = len(covers_wanted) + (0 if args.no_covers else len(pages))  # the saved ones, and one for each new album
        print(f"{color.role('To do', 'title', on)}: {shown(len(pages))} album page(s) (+ their series pages), {shown(cover_count)} cover(s); "
              f"about {human_size(len(pages) * PAGE_BYTES + cover_count * COVER_BYTES)} on disk (pages are stored compressed; "
              "covers in `cover.dir`)", file=out)
        if args.pretend or not (pages or covers_wanted):
            if not (pages or covers_wanted):
                print(color.role("Nothing to fetch: the library holds everything the album list has.", "good", on), file=out)
            return 0
        if args.limit:
            print(f"This run: the first {shown(min(args.limit, len(pages)))}; `--limit 0` fetches all that are left.", file=out)
        banner("Fetching the albums (Ctrl-C stops; run it again to go on)", stream=bar_stream)
        total = (min(args.limit, len(pages)) if args.limit else len(pages)) + len(covers_wanted)
        bar = Bar(total, "Mirror", stream=bar_stream)
        last = [0]

        def progress(done: int, count: int, detail: str) -> None:
            if done > last[0]:
                bar.update(detail, step=done - last[0])
                last[0] = done
            else:
                bar.refresh(detail)

        engine = mirror.Mirror(source, library, settings, workers=args.workers, with_covers=not args.no_covers, progress=progress)
        try:
            result = engine.run(pages, covers_wanted, args.limit)
        finally:
            bar.finish()
    print(f"{color.role('Done', 'good', on)}: {shown(result.albums)} album(s) stored, {shown(result.covers)} cover(s) saved"
          + (f", {color.role(str(len(result.failed)), 'bad', on)} failed" if result.failed else "")
          + (f"; {shown(result.left)} left for the next run" if result.left else "") + ".", file=out)
    for address, why in result.failed[:10]:
        print(f"  {color.role('failed', 'bad', on)}: {address}: {why}", file=out)
    if len(result.failed) > 10:
        print(f"  ... and {len(result.failed) - 10} more (`-vv` says all)", file=out)
    if result.stopped:
        print(color.role(result.stopped, "warn", on), file=out)
    return 1 if result.stopped and result.failed else 0
