"""``bdnex init``: download (or refresh) the album list the matching works from."""

from __future__ import annotations

import argparse
import sys

from bdnex.importer.bedetheque.fetchers import FetcherError
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.library import Library
from bdnex.ui import context
from bdnex.ui.output import Bar, add_verbosity, setup_logging

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


def run(argv: list[str], out=sys.stdout, bar_stream=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex init", description=__doc__,
        epilog="The matching of file names works offline from this list (about 55 files, 70 MB). Run it "
               "once before your first import, and again whenever you want to pick up new albums.")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    settings = context.settings(library=False)
    if settings is None:
        return 1
    with Library.for_settings(settings) as library:
        try:
            source = create_source(settings.source, library, settings)
        except (UnknownSource, FetcherError, TypeError) as err:
            print(f"{err}\nCheck `source` in your configuration (`bdnex config --edit`).", file=sys.stderr)
            return 1
        try:
            before, after = download(source, library, out, bar_stream)
        except source.fetch_errors as err:
            print(f"Could not download the album list: {err}", file=sys.stderr)
            return 1
    news = f" ({after - before:+,} since the last time)" if before else ""
    print(f"Done: {after:,} albums in the list{news}.", file=out)
    print("Next: `bdnex import <folder>`.", file=out)
    return 0
