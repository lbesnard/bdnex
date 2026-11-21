"""``bdnex init --mirror``: keep the whole catalogue (every album page, series page and cover) in the library.

The old ``bdnex mirror`` is now this option of ``init`` (the album list it fetches is what the mirror works from)."""

from __future__ import annotations

import argparse
import sys

from bdnex.formatting import human_size
from bdnex.importer import mirror
from bdnex.ui.output import Bar, add_pretend, add_workers, banner

PAGE_BYTES = 30_000  # an album page once compressed, about (the estimate of the preview)
COVER_BYTES = 100_000  # a cover image, about


def add_options(parser: argparse.ArgumentParser) -> None:
    """The options of ``bdnex init --mirror``."""
    parser.add_argument("--limit", type=int, default=0, metavar="N",
                        help="with --mirror: fetch at most N albums this time (0: all that are left)")
    parser.add_argument("--no-covers", action="store_true", help="with --mirror: the pages only, do not download the covers")
    parser.add_argument("--covers-only", action="store_true",
                        help="with --mirror: only the covers of the albums the library already holds without one (no page is fetched)")
    add_workers(parser, "album pages fetched with --mirror", "bedetheque.parallel")
    add_pretend(parser, "be fetched by --mirror: how many albums and covers are missing, and about how much room they take")
    parser.add_argument("--yes", action="store_true", help="with --mirror: do not ask before fetching (a script, or the old `bdnex mirror`)")


def check_options(args, parser: argparse.ArgumentParser) -> None:
    """The mirror options mean nothing without ``--mirror``: say so rather than ignore them."""
    if not args.mirror:
        used = [flag for flag, value in (("--limit", args.limit), ("--no-covers", args.no_covers), ("--covers-only", args.covers_only),
                                         ("--workers", args.workers is not None), ("-n", args.pretend), ("--yes", args.yes)) if value]
        if used:
            parser.error(f"{', '.join(used)}: only for `bdnex init --mirror`")
    if args.no_covers and args.covers_only:
        parser.error("--covers-only and --no-covers cancel each other")


def mirror_albums(args, settings, library, source, out=sys.stdout, bar_stream=None, ask=None) -> int:
    """Fetch what the album list has and the library lacks. ``ask(question) -> bool`` is asked before anything is fetched
    (``None``: no question). Returns the exit code."""
    from bdnex.ui import color

    on = color.enabled(out)
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
    size = human_size(len(pages) * PAGE_BYTES + cover_count * COVER_BYTES)
    print(f"{color.role('To do', 'title', on)}: {shown(len(pages))} album page(s) (+ their series pages), {shown(cover_count)} cover(s); "
          f"about {size} on disk (pages are stored compressed; covers in `cover.dir`)", file=out)
    if args.pretend or not (pages or covers_wanted):
        if not (pages or covers_wanted):
            print(color.role("Nothing to fetch: the library holds everything the album list has.", "good", on), file=out)
        return 0
    if ask is not None and not ask(f"Fetch {len(pages):,} album page(s) and {cover_count:,} cover(s) from the site, about {size}? "
                                   "It takes a long time (Ctrl-C stops, the next run goes on)"):
        print("Nothing fetched.", file=out)
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
