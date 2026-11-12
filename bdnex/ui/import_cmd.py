"""``bdnex import``: match files to Bédéthèque albums and record them in the library."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from bdnex.config import ConfigError, load_config
from bdnex.importer.bedetheque.fetchers import FetcherError
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.importer.session import (ALREADY, DUPLICATE, FAILED, OUTCOME_LABELS, PARTIAL, PRETEND, SKIPPED, TAGGED,
                                    ImportSession, NeedsTerminal)
from bdnex.library import Library
from bdnex.ui import describe
from bdnex.ui.output import Bar, add_verbosity, progress, setup_logging
from bdnex.ui.prompts import InquirerPrompter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bdnex import", description=__doc__,
        epilog="With neither --match nor --fetch, both steps run. A step that is already done for a file "
               "is skipped (the report says so); --force redoes it.")
    parser.add_argument("paths", nargs="*", help="comic files or folders (with --fetch alone: optional, "
                                                 "default everything in the library)")
    steps = parser.add_argument_group("steps (default: both)")
    steps.add_argument("-m", "--match", action="store_true",
                       help="find the album of each file, from the album list (offline; run `bdnex init` first)")
    steps.add_argument("-f", "--fetch", action="store_true",
                       help="read the album pages: authors, publisher, summary, genres... (slow, politely)")
    parser.add_argument("--force", action="store_true",
                        help="redo the selected steps for files that are already done (rematch, read the pages again)")
    parser.add_argument("--auto", choices=["strong", "all"], default=None, metavar="[=all]",
                        help="do not ask about confident matches (score >= match.strong_threshold); "
                             "--auto=all never asks: medium matches are applied too, the rest skipped")
    parser.add_argument("--no-auto", dest="auto", action="store_const", const=False,
                        help="check every file, whatever import.auto says")
    parser.add_argument("-n", "--pretend", action="store_true",
                        help="show what would be done; change nothing")
    parser.add_argument("-W", "--write", action="store_true",
                        help="afterwards, write ComicInfo.xml into the imported files (in place)")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--move", action="store_true",
                       help="afterwards, write tags and move the files into the library tree")
    where.add_argument("--copy", action="store_true",
                       help="afterwards, copy the files into the library tree (originals untouched)")
    where.add_argument("--no-move", action="store_true",
                       help="leave files from outside your library folder where they are (`import.outside`); "
                            "`bdnex move staged:true` places them later")
    parser.add_argument("--cover-check", action=argparse.BooleanOptionalAction, default=None,
                        help="compare each file's cover with its album's (one request per album; needs "
                             "`pip install 'bdnex[cover]'`); default: `cover.check`")
    parser.add_argument("--workers", type=int, metavar="N",
                        help="processes matching file names in the background (0 = CPU cores - 1, 1 = none; "
                             "default: `import.workers`)")
    add_verbosity(parser)
    legacy = parser.add_argument_group("old names (still work)")
    legacy.add_argument("--no-fetch", action="store_true", help="same as --match")
    legacy.add_argument("--retry-partial", action="store_true", help="same as --fetch")
    legacy.add_argument("--recache", action="store_true", help="same as --fetch --force")
    return parser


def resolve_steps(args) -> tuple[tuple[str, ...], list[str]]:
    """The steps to run and the notices about old option names."""
    notes = []
    for old, new in (("no_fetch", "--match"), ("retry_partial", "--fetch"), ("recache", "--fetch --force")):
        if getattr(args, old):
            notes.append(f"note: --{old.replace('_', '-')} is now {new}")
            if old == "recache":
                args.fetch = args.force = True
            else:
                setattr(args, "match" if old == "no_fetch" else "fetch", True)
    chosen = tuple(step for step in ("match", "fetch") if getattr(args, step))
    return chosen or ("match", "fetch"), notes


def ensure_album_list(source, library, settings, out=sys.stdout) -> bool:
    """The album list is needed for matching: offer to download it. False when there is none."""
    if source.is_ready():
        return True
    from bdnex.ui import init_cmd

    print("There is no album list yet: file names are matched against it (`bdnex init` downloads it).",
          file=sys.stderr)
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        print("Run `bdnex init`, then import again.", file=sys.stderr)
        return False
    if not InquirerPrompter().confirm("Download it now (about 70 MB, around a minute)?", default=True):
        print("Nothing done. `bdnex init` downloads it whenever you like.", file=sys.stderr)
        return False
    try:
        init_cmd.download(source, library, out)
    except source.fetch_errors as err:
        print(f"Could not download the album list: {err}", file=sys.stderr)
        return False
    return True


def run(argv: list[str], out=sys.stdout) -> int:
    # a bare `--auto` must not take the next word (a path) as its value: only `--auto=all` has one
    argv = ["--auto=strong" if arg == "--auto" else arg for arg in argv]
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    steps, notes = resolve_steps(args)
    for note in notes:
        print(note, file=sys.stderr)
    if "match" in steps and not args.paths:
        print("Give the files or folders to match, e.g. `bdnex import ~/comics`.", file=sys.stderr)
        return 2
    try:
        settings = load_config().settings
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1

    with Library.for_settings(settings) as library:
        try:
            source = create_source(settings.source, library, settings)
        except (UnknownSource, FetcherError, TypeError) as err:
            print(f"{err}\nCheck `source` and `bedetheque.fetcher` in your configuration (`bdnex config --edit`).", file=sys.stderr)
            return 1
        if "match" in steps and not ensure_album_list(source, library, settings, out):
            return 1
        session = ImportSession(library, source, settings, prompter=InquirerPrompter(),
                                pretend=args.pretend, force=args.force, auto=args.auto, steps=steps,
                                workers=args.workers, cover_check=args.cover_check, progress=progress, bar=lambda total, label: Bar(total, label))
        try:
            report = session.run(args.paths)
        except NeedsTerminal:
            print("\nThis import has to ask you about a file, and there is no terminal to ask on.\n"
                  "Run it in a terminal, or add --auto (confident matches are applied, the others wait),\n"
                  "--auto=all (never ask), or -n (preview). What was decided so far is saved.", file=sys.stderr)
            return 2
        try:
            organize_results, placement_notes = (_place(args, settings, library, report) if not args.pretend else ([], []))
        except ConfigError as err:
            print(f"The import was recorded, but the files were not touched: {err}", file=sys.stderr)
            organize_results, placement_notes = [], []
            organize_failed = True
        else:
            organize_failed = False

    for r in report.results:
        if r.outcome == ALREADY and not args.verbose and "not fetched yet" not in r.note:
            continue  # thousands of "already imported" lines would hide the rest; the summary counts them
        score = f"{r.score:5.1f}" if r.score is not None else "  -  "
        print(f"{OUTCOME_LABELS.get(r.outcome, r.outcome):<9} {score}  {r.path}", file=out)
        if r.album_id is not None and r.outcome in ("tagged", "partial", "skipped", "duplicate"):
            with Library.for_settings(settings) as library:
                album = library.get_album(r.album_id)
            if album:
                print(f"             -> {describe.short(album)}", file=out)
        if r.note:
            print(f"             {r.note}", file=out)
    if report.results:
        print("\nSee everything that was recorded with `bdnex ls --long` (add `--files` for the files).", file=out)
    order = (TAGGED, PARTIAL, ALREADY, SKIPPED, DUPLICATE, FAILED, PRETEND)
    summary = ", ".join(f"{report.count(o)} {OUTCOME_LABELS.get(o, o)}" for o in order if report.count(o))
    print(f"\n{len(report.results)} file(s): {summary or 'nothing to do'}", file=out)
    if report.count(ALREADY) and not args.verbose:
        print("(`-v` lists the files that were already imported; `--force` redoes them.)", file=out)
    if report.count(PARTIAL) and "fetch" in steps:
        print("Some albums could not be completed (the page was not available): `bdnex import --fetch` tries again.", file=out)
    elif report.count(PARTIAL):
        print("The albums are matched but their details are not read yet: `bdnex import --fetch` does it.", file=out)
    if organize_results:
        from bdnex.ui.write_cmd import report as organizer_report
        print("\nFiles:", file=out)
        organizer_report(organize_results, out)
    for note in placement_notes:
        print(note, file=out)
    return 1 if organize_failed or report.count(FAILED) or any(r.outcome == "failed" for r in organize_results) else 0


def _outside(rows, root) -> tuple[list, list]:
    """Split file rows into (outside the library folder, inside it)."""
    if not root:
        return [], list(rows)
    inside_prefix = root + os.sep
    outside = [r for r in rows if not r["path"].startswith(inside_prefix)]
    return outside, [r for r in rows if r["path"].startswith(inside_prefix)]


def _ask_outside(count: int, directory: str, prompter) -> str:
    """One question for the whole run: what to do with the files that came from elsewhere."""
    if not sys.stdin.isatty():
        return "leave"
    return prompter.choose_placement(count, directory)


def _place(args, settings, library, report, prompter=None):
    """File operations after the import. Returns (results, notes).

    Flags win over the configuration: ``--move`` / ``--copy`` apply to every imported file, ``--no-move``
    leaves the ones from outside the library folder where they are, ``-W`` writes tags in place. Without a
    flag, ``import.mode: copy|move`` applies to everything, and otherwise the files imported from outside
    the library folder follow ``import.outside`` (asked once per run).
    """
    from bdnex.organizer.organizer import COPY, MOVE, WRITE, Organizer

    rows = [row for row in (library.get_file(r.path) for r in report.results if r.outcome in ("tagged", "partial")) if row]
    outside, inside = _outside(rows, library.root)
    plan, leave, notes = [], [], []
    configured = {"copy": COPY, "move": MOVE}.get(settings.import_.mode)
    if args.move or args.copy or (configured and not args.no_move):
        plan.append((rows, MOVE if args.move else COPY if args.copy else configured))
    else:
        if args.write:
            plan.append((inside, WRITE))
        if outside:
            choice = "leave" if args.no_move else settings.import_.outside
            if choice == "ask":
                prompter = prompter or InquirerPrompter()
                choice = _ask_outside(len(outside), str(library.root), prompter)
                if choice == "leave" and not sys.stdin.isatty():
                    notes.append("(No terminal to ask on: the files from outside your library folder were left "
                                 "where they are. `import.outside` or `--move` / `--copy` decides in advance.)")
            if choice in ("move", "copy"):
                plan.append((outside, MOVE if choice == "move" else COPY))
            else:
                leave = outside
                if args.write:
                    plan.append((outside, WRITE))
    if any(mode != WRITE for _, mode in plan):
        settings.require_library_directory()
    never_ask = args.auto == "all"
    organizer = Organizer(library, settings, prompter=None if never_ask else InquirerPrompter(), force=never_ask)
    results = []
    for chosen, mode in plan:
        results += organizer.run(chosen, mode, progress=progress)
    for row in leave:
        library.stage_file(row["path"])
    if leave:
        notes.append(f"{len(leave)} file(s) from outside {library.root} were left where they are. "
                     "`bdnex move staged:true` moves them into the library whenever you like.")
    return results, notes
