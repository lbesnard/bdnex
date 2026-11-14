"""``bdnex import``: match files to Bédéthèque albums and record them in the library."""

from __future__ import annotations

import argparse
import os
import sys

from bdnex.config import ConfigError
from bdnex.importer.bedetheque.fetchers import FetcherError
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.importer.session import (ALREADY, DUPLICATE, FAILED, OUTCOME_LABELS, PARTIAL, PRETEND, SKIPPED, TAGGED,
                                    ImportSession, NeedsTerminal)
from bdnex.library import Library
from bdnex.ui import color, context, describe, results
from bdnex.ui.output import Bar, add_pretend, add_verbosity, add_workers, banner, progress, setup_logging
from bdnex.ui.prompts import can_ask, new_prompter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bdnex import", description=__doc__,
        epilog="With neither --match nor --fetch, both steps run. A step that is already done for a file "
               "is skipped (the report says so); --force redoes it.")
    parser.add_argument("paths", nargs="*", help="comic files or folders (default: your library folder, `collection_path` in the "
                                                 "configuration; with --fetch alone: everything in the library)")
    steps = parser.add_argument_group("steps (default: both)")
    steps.add_argument("-m", "--match", action="store_true",
                       help="find the album of each file, from the album list (offline; run `bdnex init` first)")
    steps.add_argument("-f", "--fetch", action="store_true",
                       help="read the album pages: authors, publisher, summary, genres... (slow, politely)")
    parser.add_argument("--skipped", "--skipped-earlier", dest="skipped", action="store_true",
                        help="only the files you (or the matching) skipped at an earlier import, asked about again; "
                             "`bdnex ls --unmatched` lists them")
    parser.add_argument("--sources", action="store_true",
                        help="the folders of `import.sources` in the configuration: what is new in them is copied or "
                             "moved into the collection, each source in its own mode. Added to the paths, if any")
    parser.add_argument("-s", "--select", action="append", default=[], metavar="TERM",
                        help="work on the files already in the library that match this `bdnex ls --files` term "
                             "(repeatable, all must match): `--select tagged:false`, `--select 'author:uderzo'`. "
                             "Added to the paths, if any")
    parser.add_argument("--as-is", action="store_true",
                        help="only record the files: name, path, format, size, pages, checksum and what their own "
                             "ComicInfo.xml says. No matching, no network, nothing moved or written. "
                             "`bdnex import` (matching) can be run on them later")
    parser.add_argument("--unattended", action="store_true",
                        help="ask nothing: apply the confident matches (`--auto`), leave the others skipped for you "
                             "(`--skipped` asks about them later), no question about duplicates, name clashes or conflicts "
                             "(the database wins, a clash gets a number, originals are kept). What `bdnex serve` uses to watch folders")
    parser.add_argument("--force", action="store_true",
                        help="redo the selected steps for files that are already done (rematch, read the pages again)")
    parser.add_argument("--auto", choices=["strong", "all"], default=None, metavar="[=all]",
                        help="do not ask about confident matches (score >= match.strong_threshold); "
                             "--auto=all never asks: medium matches are applied too, the rest skipped")
    parser.add_argument("--no-auto", dest="auto", action="store_const", const=False,
                        help="check every file, whatever import.auto says")
    add_pretend(parser)
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
    parser.add_argument("--database-only", action="store_true",
                        help="only fill the library: no comic file is moved, copied, written, converted, replaced or "
                             "deleted, whatever the configuration says (a duplicate is skipped, not replaced)")
    parser.add_argument("--cover-check", action=argparse.BooleanOptionalAction, default=None,
                        help="compare each file's cover with its album's (one request per album; needs "
                             "`pip install 'bdnex[cover]'`); default: `cover.check`")
    series = parser.add_mutually_exclusive_group()
    series.add_argument("--series", dest="series", action="store_const", const="always", default=None,
                        help="assume the files of a folder are the volumes of one series, without asking "
                             "(default: asked when the names do not make it obvious; `import.series`)")
    series.add_argument("--no-series", dest="series", action="store_const", const="never",
                        help="match every file by itself, never as a folder")
    add_workers(parser, "processes matching file names in the background, and reading the files for --as-is")
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
    if not can_ask():
        print("Run `bdnex init`, then import again.", file=sys.stderr)
        return False
    if not new_prompter().confirm("Download it now (about 70 MB, around a minute)?", default=True):
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.database_only:
        if args.write or args.move or args.copy:
            parser.error("--database-only changes no file: it can not be combined with --write, --move or --copy")
        args.no_move = True  # nothing from outside the collection is moved or copied either
    setup_logging(args.verbose)
    steps, notes = resolve_steps(args)
    for note in notes:
        print(note, file=sys.stderr)
    settings = context.settings(library=False)
    if settings is None:
        return 1
    if args.as_is:
        return _record_as_is(args, settings, out)
    if args.skipped:
        code = _skipped_paths(args, settings)
        if code:
            return code
    if args.sources:
        if not settings.import_.sources:
            print("No source is configured: list folders under `import.sources` in your configuration "
                  "(`bdnex config --edit`).", file=sys.stderr)
            return 2
        args.paths = [*args.paths, *(source.folder for source in settings.import_.sources)]
    if args.select:
        code = _select_paths(args, settings)
        if code:
            return code
    if "match" in steps and not args.paths:  # no folder given: look at the library folder of the configuration
        try:
            args.paths = [str(settings.require_collection())]
        except ConfigError:
            print("Give the files or folders to match, e.g. `bdnex import ~/comics`\n"
                  "(without one, bdnex looks at `collection_path` from your configuration, which is not set:\n"
                  "`bdnex config --edit`).", file=sys.stderr)
            return 2
        print(f"No folder given: importing your library folder, {args.paths[0]}", file=sys.stderr)

    with Library.for_settings(settings) as library:
        try:
            source = create_source(settings.source, library, settings)
        except (UnknownSource, FetcherError, TypeError) as err:
            print(f"{err}\nCheck `source` and `bedetheque.fetcher` in your configuration (`bdnex config --edit`).", file=sys.stderr)
            return 1
        from bdnex.importer.bedetheque.fetchers import uses_saved_pages

        if "fetch" in steps and uses_saved_pages(settings) and not args.pretend:
            why = ("no `bedetheque.fetcher` is set" if not settings.bedetheque.fetcher
                   else "`bedetheque.fetcher_endpoint` is not set for the default fetcher (FlareSolverr)")
            print(f"note: {why}, so album pages are read from {settings.pages_dir}: save each page there as "
                  "<album id>.html. Pages that are not there are reported as not fetched. To fetch pages, set "
                  "`bedetheque.fetcher_endpoint` (or your own `bedetheque.fetcher`): `bdnex config --edit`.",
                  file=sys.stderr)
        if "match" in steps and not ensure_album_list(source, library, settings, out):
            return 1
        steps_order = [step for step in ("match", "fetch") if step in steps]
        session = ImportSession(library, source, settings, prompter=None if args.unattended else new_prompter(source=source, thumbnails=settings.import_.thumbnails),
                                pretend=args.pretend, force=args.force, auto=args.auto, steps=steps, retry_skipped=getattr(args, "retry_skipped", False),
                                workers=args.workers, cover_check=args.cover_check, series=args.series, files_untouched=args.database_only, progress=progress, bar=lambda total, label: Bar(total, label),
                                phase=lambda step, title: banner(title, steps_order.index(step) + 1, len(steps_order)))
        try:
            report = session.run(args.paths)
        except NeedsTerminal:
            print("\nThis import has to ask you about a file, and there is no terminal to ask on.\n"
                  "Run it in a terminal, or add --auto (confident matches are applied, the others wait),\n"
                  "--auto=all (never ask), or -n (preview). What was decided so far is saved.", file=sys.stderr)
            return 2
        try:
            if report.aborted_at:
                organize_results, placement_notes = [], _after_abort(library, report)
            else:
                organize_results, placement_notes = (_place(args, settings, library, report) if not args.pretend else ([], []))
        except ConfigError as err:
            print(f"The import was recorded, but the files were not touched: {err}", file=sys.stderr)
            organize_results, placement_notes = [], []
            organize_failed = True
        else:
            organize_failed = False
        to_review = library.query(["review:true"], files=True)  # all of them, whichever run flagged them

    if report.results:
        banner("Result", stream=out)
    for r in report.results:
        if r.outcome == ALREADY and not args.verbose and "not fetched yet" not in r.note:
            continue  # thousands of "already imported" lines would hide the rest; the summary counts them
        on = color.enabled(out)
        score = color.paint_score(f"{r.score:5.1f}", r.score, on) if r.score is not None else "  -  "
        label = OUTCOME_LABELS.get(r.outcome, r.outcome)
        print(f"{color.paint(f'{label:<9}', results.style(r.outcome), on)} {score}  {r.path}", file=out)
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
    again = sum(1 for r in report.results if r.note.startswith("skipped earlier"))
    if again:
        print(f"{again} file(s) were skipped at an earlier import and left alone: `bdnex import --skipped` asks about "
              "just those again (run `bdnex init` first if new albums were added).", file=out)
    if to_review:
        print(f"{len(to_review)} file(s) have a cover that does not look like their album's: "
              "`bdnex review` shows them side by side.", file=out)
    if report.count(ALREADY) and not args.verbose:
        print("(`-v` lists the files that were already imported; `--force` redoes them.)", file=out)
    if report.count(PARTIAL) and "fetch" in steps:
        print("Some albums could not be completed (the page was not available): `bdnex import --fetch` tries again.", file=out)
    elif report.count(PARTIAL):
        print("The albums are matched but their details are not read yet: `bdnex import --fetch` does it.", file=out)
    if organize_results:
        from bdnex.ui.write_cmd import report as organizer_report
        banner("Files", stream=out)
        organizer_report(organize_results, out)
    for note in placement_notes:
        print(note, file=out)
    if report.aborted_at:
        print(f"\nAborted at {os.path.basename(report.aborted_at)} ({len(report.results)} of {report.total} files looked at). "
              "Everything decided so far is saved and no album page was fetched: run the same command again to continue "
              "from there.", file=out)
        return 1
    return 1 if organize_failed or report.count(FAILED) or any(r.outcome == "failed" for r in organize_results) else 0


def _skipped_paths(args, settings) -> int:
    """``--skipped``: the files skipped at an earlier import (and still there) become the files to work on."""
    if not context.has_library(settings, "nothing was skipped."):
        return 1
    with Library.for_settings(settings, readonly=True) as library:
        rows = [row for row in library.unmatched() if row["outcome"] == "skipped"]
    found = [row["path"] for row in rows if os.path.exists(row["path"])]
    if not found:
        print("No file was skipped at an earlier import" + (" (and is still there)." if rows else "."), file=sys.stderr)
        return 1
    print(f"--skipped: {len(found)} file(s) skipped earlier" + (f" ({len(rows) - len(found)} no longer on disk)" if len(rows) > len(found) else ""),
          file=sys.stderr)
    args.paths = [*args.paths, *found]
    args.retry_skipped = True
    return 0


def _select_paths(args, settings) -> int:
    """``--select``: the library files that match the terms become the files to work on."""
    from bdnex.library.query import QueryError

    if not context.has_library(settings, "nothing to select from."):
        return 1
    try:
        with Library.for_settings(settings, readonly=True) as library:
            rows = library.query(args.select, files=True)
    except QueryError as err:
        print(f"bad --select query: {err}", file=sys.stderr)
        return 2
    if not rows:
        print("No file in the library matches --select " + " ".join(args.select), file=sys.stderr)
        return 1
    print(f"--select: {len(rows)} file(s) of the library", file=sys.stderr)
    args.paths = [*args.paths, *(row["path"] for row in rows)]
    return 0


def _record_as_is(args, settings, out) -> int:
    """``--as-is``: put the files in the library without matching them to an album."""
    from bdnex.library.scan import COMIC_EXTENSIONS, iter_comic_files

    paths = args.paths
    if not paths:
        try:
            paths = [str(settings.require_collection())]
        except ConfigError as err:
            print(f"Give the files or folders to record, e.g. `bdnex import --as-is ~/comics`: {err}", file=sys.stderr)
            return 2
        print(f"No folder given: recording your library folder, {paths[0]}", file=sys.stderr)
    files = []
    for path in paths:
        if not os.path.exists(path):
            print(f"{path!r} is not an existing file or folder", file=sys.stderr)
        elif os.path.isdir(path):
            files.extend(iter_comic_files(path))
        elif path.lower().endswith(COMIC_EXTENSIONS):
            files.append(os.path.abspath(path))
    counts = {"recorded": 0, "known": 0, "failed": 0, "with ComicInfo.xml": 0, "damaged": 0}
    with Library.for_settings(settings, readonly=args.pretend) as library:
        todo = []
        for path in files:
            if library.get_file(path) and not args.force:
                counts["known"] += 1
            else:
                todo.append(path)
        if args.pretend:
            counts["recorded"] = len(todo)
        else:
            from bdnex.importer.pipeline import resolve_workers
            from bdnex.library.database import analyse_many

            workers = resolve_workers(settings.import_.workers if args.workers is None else args.workers)
            for number, (path, analysed) in enumerate(analyse_many(todo, workers), start=1):
                progress(f"[{number}/{len(todo)}] {os.path.basename(path)}")
                if isinstance(analysed, Exception):  # one bad file must not stop the batch
                    counts["failed"] += 1
                    print(f"{path}: {analysed}", file=sys.stderr)
                    continue
                info, md5 = analysed
                library.add_analysed(info, md5)
                library.log_import(path, "registered")
                counts["recorded"] += 1
                counts["with ComicInfo.xml"] += bool(info.embedded)
                counts["damaged"] += bool(info.corrupted)
    verb = "would be recorded" if args.pretend else "recorded"
    print(f"\n{len(files)} file(s): {counts['recorded']} {verb}, {counts['known']} already in the library, "
          f"{counts['failed']} failed", file=out)
    if not args.pretend and counts["recorded"]:
        print(f"{counts['with ComicInfo.xml']} have a ComicInfo.xml of their own, {counts['damaged']} look damaged. "
              "They are not matched yet: `bdnex ls --files tagged:false` lists them, and `bdnex import` (without "
              "--as-is) matches and fetches them whenever you like.", file=out)
    return 1 if counts["failed"] else 0


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


def _after_abort(library, report) -> list[str]:
    """Nothing is moved after an abort. Files imported from elsewhere are noted as staged, so that
    `bdnex move staged:true` can still place them."""
    rows = [row for row in (library.get_file(r.path) for r in report.results if r.outcome in ("tagged", "partial")) if row]
    outside, _ = _outside(rows, library.root)
    for row in outside:
        library.stage_file(row["path"])
    if not outside:
        return []
    return [f"{len(outside)} file(s) imported from outside {library.root} were left where they are. "
            "`bdnex move staged:true` moves them into the library whenever you like."]


def _place(args, settings, library, report, prompter=None):
    """File operations after the import. Returns (results, notes).

    Flags win over the configuration: ``--move`` / ``--copy`` apply to every imported file, ``--no-move``
    leaves the ones from outside the library folder where they are, ``-W`` writes tags in place. Without a
    flag, ``import.mode: copy|move`` applies to everything, and otherwise the files imported from outside
    the library folder follow ``import.outside`` (asked once per run).
    """
    from bdnex.organizer.organizer import COPY, MOVE, WRITE, Organizer

    rows = [row for row in (library.get_file(r.path) for r in report.results if r.outcome in ("tagged", "partial")) if row]
    plan, leave, notes = [], [], []
    from_sources = []  # rows under a configured source: that source's mode decides, unless a flag does
    if not (args.move or args.copy):
        from_sources = [row for row in rows if settings.source_for(row["path"])]
        rows = [row for row in rows if not settings.source_for(row["path"])]
    if args.no_move:
        leave += from_sources
    else:
        for mode_name, chosen_mode in (("copy", COPY), ("move", MOVE)):
            group = [row for row in from_sources if settings.source_for(row["path"]).mode == mode_name]
            if group:
                plan.append((group, chosen_mode))
    outside, inside = _outside(rows, library.root)
    configured = {"copy": COPY, "move": MOVE}.get(settings.import_.mode)
    if args.move or args.copy or (configured and not args.no_move):
        plan.append((rows, MOVE if args.move else COPY if args.copy else configured))
    else:
        if args.write:
            plan.append((inside, WRITE))
        if outside:
            choice = "leave" if args.no_move else settings.import_.outside
            if choice == "ask":
                prompter = prompter or new_prompter()
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
        settings.require_collection()
    never_ask = args.auto == "all" or args.unattended
    organizer = Organizer(library, settings, prompter=None if never_ask else new_prompter(), force=never_ask)
    results = []
    for chosen, mode in plan:
        results += organizer.run(chosen, mode, progress=progress)
        if organizer.aborted_at:
            from bdnex.ui.write_cmd import aborted_message
            notes.append(aborted_message(organizer.aborted_at, "move"))
            break
    for row in leave:
        library.stage_file(row["path"])
    if leave:
        notes.append(f"{len(leave)} file(s) from outside {library.root} were left where they are. "
                     "`bdnex move staged:true` moves them into the library whenever you like.")
    return results, notes
