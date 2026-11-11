"""``bdnex write`` and ``bdnex move``: push library metadata into files."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from bdnex.config import ConfigError, load_config
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.organizer.organizer import COPY, FAILED, MOVE, WRITE, Organizer
from bdnex.ui.output import add_verbosity, progress, setup_logging
from bdnex.ui.prompts import InquirerPrompter

OUTCOMES = ("written", "moved", "copied", "unchanged", "skipped", "failed", "pretend")


def _parser(command: str) -> argparse.ArgumentParser:
    if command == "write":
        doc = ("Write ComicInfo.xml into the files where they are. CBR and PDF files are "
               "converted to CBZ next to the original (see write.cbr / write.pdf).")
    else:
        doc = ("Write ComicInfo.xml and rename the files into the library tree "
               "(`directory`, `paths` in the config).")
    parser = argparse.ArgumentParser(prog=f"bdnex {command}", description=doc)
    parser.add_argument("query", nargs="*", help="files to process, same terms as `bdnex ls --files`")
    parser.add_argument("-n", "--pretend", action="store_true", help="show what would be done; change nothing")
    parser.add_argument("--force", action="store_true",
                        help="never ask: the database wins, name clashes get a number, originals are kept")
    if command == "move":
        parser.add_argument("--copy", action="store_true",
                            help="leave the source files untouched; the copy is what the database tracks")
    add_verbosity(parser)
    return parser


def report(results, out) -> int:
    for r in results:
        line = f"{r.outcome:<9} {r.path}"
        if r.dest and r.dest != r.path:
            line += f"\n          -> {r.dest}"
        if r.note:
            line += f"\n          {r.note}"
        print(line, file=out)
    summary = ", ".join(f"{sum(1 for r in results if r.outcome == o)} {o}" for o in OUTCOMES
                        if any(r.outcome == o for r in results))
    print(f"\n{len(results)} file(s): {summary or 'nothing to do'}", file=out)
    return 1 if any(r.outcome == FAILED for r in results) else 0


def run(command: str, argv: list[str], out=sys.stdout) -> int:
    args = _parser(command).parse_args(argv)
    setup_logging(args.verbose)
    mode = WRITE if command == "write" else (COPY if args.copy else MOVE)
    try:
        settings = load_config().settings
        if mode != WRITE:
            settings.require_library_directory()
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    if not settings.library_path.exists():
        print(f"No library yet ({settings.library_path}): run `bdnex import <folder>` first.", file=sys.stderr)
        return 1

    with Library(settings.library_path) as library:
        try:
            rows = library.query([*args.query, "tagged:true"], files=True)
        except QueryError as err:
            print(f"bad query: {err}", file=sys.stderr)
            return 2
        organizer = Organizer(library, settings, prompter=None if args.force else InquirerPrompter(),
                              pretend=args.pretend, force=args.force)
        results = organizer.run(rows, mode, progress=progress)
    return report(results, out)


def run_write(argv: list[str], out=sys.stdout) -> int:
    return run("write", argv, out)


def run_move(argv: list[str], out=sys.stdout) -> int:
    return run("move", argv, out)
