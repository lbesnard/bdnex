"""``bdnex import``: match files to Bédéthèque albums and record them in the library."""

from __future__ import annotations

import argparse
import logging
import sys

from bdnex.config import ConfigError, load_config
from bdnex.importer.bedetheque.fetchers import FetcherError
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.importer.session import ImportSession
from bdnex.library import Library
from bdnex.ui.output import add_verbosity, progress, setup_logging
from bdnex.ui.prompts import InquirerPrompter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bdnex import", description=__doc__)
    parser.add_argument("paths", nargs="+", help="comic files or folders to import")
    parser.add_argument("-n", "--pretend", action="store_true",
                        help="show what would be matched; change nothing")
    parser.add_argument("--force", action="store_true",
                        help="never ask: apply the best match (strong or medium), re-import known files")
    parser.add_argument("-W", "--write", action="store_true",
                        help="after importing, write ComicInfo.xml into the imported files (in place)")
    parser.add_argument("--move", action="store_true",
                        help="after importing, write tags and move the files into the library tree")
    parser.add_argument("--copy", action="store_true",
                        help="after importing, copy the files into the library tree (originals untouched)")
    add_verbosity(parser)
    parser.add_argument("--retry-partial", action="store_true",
                        help="for files recorded from the sitemap only (partial), try to fetch the album page again")
    parser.add_argument("--recache", action="store_true",
                        help="refetch sitemaps and album pages instead of using the cache")
    return parser


def run(argv: list[str], out=sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        settings = load_config().settings
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1

    with Library(settings.library_path) as library:
        try:
            source = create_source(settings.source, library, settings)
        except (UnknownSource, FetcherError, TypeError) as err:
            print(f"{err}\nCheck `source` and `bedetheque.fetcher` in your configuration (`bdnex config --edit`).", file=sys.stderr)
            return 1
        session = ImportSession(library, source, settings, prompter=InquirerPrompter(),
                                pretend=args.pretend, force=args.force, recache=args.recache, retry_partial=args.retry_partial,
                                progress=progress)
        report = session.run(args.paths)
        try:
            organize_results = _organize(args, settings, library, report) if not args.pretend else []
        except ConfigError as err:
            print(f"The import was recorded, but the files were not touched: {err}", file=sys.stderr)
            organize_results = []
            organize_failed = True
        else:
            organize_failed = False

    for r in report.results:
        score = f"{r.score:5.1f}" if r.score is not None else "  -  "
        print(f"{r.outcome:<9} {score}  {r.path}" + (f"  ({r.note})" if r.note else ""), file=out)
    summary = ", ".join(f"{report.count(o)} {o}" for o in
                        ("tagged", "partial", "skipped", "duplicate", "failed", "pretend") if report.count(o))
    print(f"\n{len(report.results)} file(s): {summary or 'nothing to do'}", file=out)
    if organize_results:
        from bdnex.ui.write_cmd import report as organizer_report
        print("\nFiles:", file=out)
        organizer_report(organize_results, out)
    return 1 if organize_failed or report.count("failed") or any(r.outcome == "failed" for r in organize_results) else 0


def _organize(args, settings, library, report):
    """File operations after the import: flags win over the `import.mode` setting."""
    from bdnex.organizer.organizer import COPY, MOVE, WRITE, Organizer

    if args.move:
        mode = MOVE
    elif args.copy:
        mode = COPY
    else:
        mode = {"copy": COPY, "move": MOVE}.get(settings.import_.mode, WRITE if args.write else None)
    if mode is None:
        return []
    if mode != WRITE:
        settings.require_library_directory()
    rows = [library.get_file(r.path) for r in report.results if r.outcome in ("tagged", "partial")]
    organizer = Organizer(library, settings, prompter=None if args.force else InquirerPrompter(), force=args.force)
    return organizer.run([r for r in rows if r], mode, progress=progress)


