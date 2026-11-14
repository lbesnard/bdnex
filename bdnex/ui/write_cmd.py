"""``bdnex write`` and ``bdnex move``: push library metadata into files."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from bdnex.config import ConfigError
from bdnex.formatting import short_path
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.organizer.organizer import auto_workers, COPY, FAILED, MOVE, WRITE, Organizer, WriteAborted
from bdnex.ui import color, context, results
from bdnex.ui.output import Bar, add_pretend, add_verbosity, add_workers, banner, setup_logging
from bdnex.ui.prompts import new_prompter

OUTCOMES = ("repaired", "written", "moved", "copied", "unchanged", "skipped", "failed", "pretend")


def _parser(command: str) -> argparse.ArgumentParser:
    if command == "convert":
        doc = ("Convert PDF (or CBR) files that are in the library to CBZ, next to the original, with the ComicInfo.xml. "
               "What happens to the original follows write.delete_original. Only files matched to an album are converted.")
    elif command == "repair":
        doc = ("Rebuild damaged archives (a page whose checksum is wrong but whose data is intact) as a clean "
               "CBZ. Only files marked corrupted are touched; ComicInfo.xml and pages are kept as they are.")
    elif command == "write":
        doc = ("Write ComicInfo.xml into the files where they are. CBR and PDF files are "
               "converted to CBZ next to the original (see write.cbr / write.pdf).")
    else:
        doc = ("Write ComicInfo.xml and rename the files into the library tree "
               "(`collection_path`, `paths` in the config).")
    parser = argparse.ArgumentParser(prog=f"bdnex {command}", description=doc)
    parser.add_argument("query", nargs="*", help="files to process, same terms as `bdnex ls --files`")
    add_pretend(parser)
    add_workers(parser, "files written", "write.workers", ", at most 4")
    parser.add_argument("--force", action="store_true",
                        help="never ask: the database wins, name clashes get a number, originals are kept")
    if command == "convert":
        parser.add_argument("--from", dest="formats", action="append", choices=["pdf", "cbr"], metavar="FORMAT",
                            help="the format to convert: pdf (the default) or cbr; repeat it for both")
    if command == "move":
        parser.add_argument("--copy", action="store_true",
                            help="leave the source files untouched; the copy is what the database tracks")
    add_verbosity(parser)
    return parser


def _changed_parts(old: str, new: str) -> tuple[str, str, str]:
    """(shared start, rest of old, rest of new), cut at a folder boundary so the difference is easy to see."""
    a, b = old.split("/"), new.split("/")
    n = 0
    while n < min(len(a), len(b)) - 1 and a[n] == b[n]:
        n += 1
    return "/".join(a[:n]) + ("/" if n else ""), "/".join(a[n:]), "/".join(b[n:])


def _steps(note: str, dest: Optional[str]) -> list[str]:
    """The 'would: ...' steps, without the 'move to <path>' part: the path has its own line."""
    steps = note.removeprefix("would: ").split("; ")
    return [step for step in steps if not (dest and step in (f"move to {dest}", f"copy to {dest}"))
            and step != "stays where it is"]


def format_result(r, root: Optional[str], on: bool, number: int = 0, total: int = 0) -> str:
    """One file's block: what happened, where it went, the steps, how long it took."""
    label = "would do" if r.outcome == "pretend" else r.outcome
    lines = [results.head(label, os.path.basename(r.path), on, r.outcome, f"[{number}/{total}]" if number else "")]
    if r.dest and r.dest != r.path:
        same, old, new = _changed_parts(short_path(r.path, root), short_path(r.dest, root))
        lines.append(f"    from  {color.paint(same, 'dim', on)}{color.paint(old, 'red', on)}")
        lines.append(f"    to    {color.paint(same, 'dim', on)}{color.paint(new, 'green', on)}")
    elif r.outcome == "pretend" and r.dest:
        lines.append(f"    {color.paint('stays where it is', 'dim', on)}  {color.paint(short_path(r.path, root), 'dim', on)}")
    steps = _steps(r.note, r.dest) if r.outcome == "pretend" else [r.note] if r.note else []
    for step in steps:
        lines.append(f"    {color.paint('•', 'dim', on)} {step}")
    if r.seconds >= 0.05 and r.outcome != "pretend":
        lines.append(f"    {color.paint(f'{r.seconds:.1f} s', 'dim', on)}")
    return "\n".join(lines)


def summary(results, out) -> int:
    on = color.enabled(out)
    counts = ", ".join(f"{sum(1 for r in results if r.outcome == o)} {o}" for o in OUTCOMES
                       if any(r.outcome == o for r in results))
    print(f"\n{color.rule()}\n{len(results)} file(s): {counts or 'nothing to do'}", file=out)
    if any(r.outcome == "pretend" for r in results):
        print("Preview only: nothing was changed. Run the same command without -n to do it.", file=out)
    failed = [r for r in results if r.outcome == FAILED]
    if failed:
        print(color.paint(f"{len(failed)} file(s) failed (listed above); the others were not affected.", "red", on), file=out)
    return 1 if failed else 0


def report(results, out, root: Optional[str] = None) -> int:
    """Every result, then the summary (for callers that did not stream the results as they came)."""
    on = color.enabled(out)
    for r in results:
        print(format_result(r, root, on), file=out)
    return summary(results, out)


class QuietPrompter:
    """Wraps the prompter so that a question starts on a clean line: the progress bar is cleared first."""

    def __init__(self, prompter, bar):
        self._prompter, self._bar = prompter, bar

    def __getattr__(self, name):
        target = getattr(self._prompter, name)
        if not callable(target):
            return target

        def ask(*args, **kwargs):
            self._bar.say("")
            return target(*args, **kwargs)
        return ask


LEGEND = ("Each file: its archive is read, a new CBZ is written with the ComicInfo.xml and the pages as they are (a PDF "
          "page that has to be drawn is a new picture, and the result says so), the new file is checked against those "
          "pages, then put in place (the original goes only after that).")


def _run_files(organizer, rows, mode, out, root, command: str, workers: int = 1, repair: bool = False) -> list:
    """Process the files showing what happens: a bar with the file and the step on a terminal, and each result as soon
    as it is known (not all of them at the end)."""
    on = color.enabled(out)
    bar = Bar(len(rows), "Files") if rows and sys.stderr.isatty() and not organizer.pretend else None
    if rows and not organizer.pretend:
        banner(f"{'Repairing' if repair else 'Converting' if command == 'convert' else 'Writing tags into' if mode == WRITE else 'Copying' if mode == COPY else 'Moving'} {len(rows)} file(s)")
        print(LEGEND, file=sys.stderr, flush=True)
    if bar:
        organizer.on_stage = lambda path, text: bar.refresh(f"{os.path.basename(path)} · {text}")
        if organizer.prompter is not None:
            organizer.prompter = QuietPrompter(organizer.prompter, bar)

    def starting(text: str) -> None:  # "[n/N] name"
        if bar:
            bar.refresh(text.split("] ", 1)[-1])

    def done(number: int, total: int, result) -> None:
        block = format_result(result, root, on, number, total)
        if bar:
            bar.say(block, stream=out)
            bar.update(os.path.basename(rows[number - 1]["path"]))
        else:
            print(block, file=out, flush=True)

    def busy(names: list[str]) -> None:  # several files at once: which ones
        if bar and names:
            bar.refresh(" · ".join(names) + f"  ({len(names)} at once)")

    if workers > 1 and rows and not organizer.pretend:
        print(f"Working on up to {workers} files at a time (`--workers`, `write.workers`). A file that needs your answer "
              "waits for its turn.", file=sys.stderr, flush=True)
    try:
        return organizer.run(rows, mode, progress=starting, on_result=done, workers=workers, on_busy=busy, repair=repair)
    finally:
        if bar:
            bar.finish()


def aborted_message(path: str, command: str = "write") -> str:
    return (f"Aborted at {path}. What was done is kept and the other files were not touched: "
            f"run `bdnex {command}` again to continue (finished files are reported as unchanged).")


def run(command: str, argv: list[str], out=sys.stdout) -> int:
    args = _parser(command).parse_args(argv)
    setup_logging(args.verbose)
    mode = WRITE if command in ("write", "repair", "convert") else (COPY if args.copy else MOVE)
    settings = context.settings(collection=mode != WRITE)
    if settings is None:
        return 1
    formats = list(dict.fromkeys(args.formats or ["pdf"])) if command == "convert" else []
    if command == "convert":
        if settings.write.format_preferred == "none":
            print("write.format_preferred is `none`: nothing is converted. Set it to `cbz` to use `bdnex convert`.",
                  file=sys.stderr)
            return 1
        for fmt in formats:  # this command is the request: the per-format switches (tag, skip) do not apply
            setattr(settings.write, fmt, "convert")

    if args.pretend:
        print("Preview: nothing is written, moved, converted or deleted, and the library is opened read-only.",
              file=sys.stderr)
    with Library.for_settings(settings, readonly=args.pretend) as library:
        try:
            if command == "convert":
                rows = [row for fmt in formats for row in library.query([*args.query, f"format:{fmt}", "tagged:true"], files=True)]
            else:
                rows = library.query([*args.query, "corrupted:true"] if command == "repair"
                                     else [*args.query, "tagged:true"], files=True)
        except QueryError as err:
            return context.bad_query(err)
        organizer = Organizer(library, settings, prompter=None if args.force else new_prompter(),
                              pretend=args.pretend, force=args.force)
        workers = args.workers if args.workers is not None else settings.write.workers
        results = _run_files(organizer, rows, mode, out, settings.collection_path, command,
                             workers or auto_workers(), repair=command == "repair")
    code = summary(results, out)
    if organizer.aborted_at:
        print(aborted_message(organizer.aborted_at, command), file=out)
    return code


def place(settings, path: str, out=sys.stdout) -> int:
    """Move one registered file to where its album belongs (`bdnex move`, for a single file)."""
    try:
        settings.require_collection()
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    with Library.for_settings(settings) as library:
        row = library.get_file(path)
        if row is None:
            print(f"{path} is not in the library.", file=sys.stderr)
            return 1
        organizer = Organizer(library, settings, prompter=new_prompter())
        try:
            results = [organizer.process(row, MOVE)]
        except WriteAborted:
            print(aborted_message(path, "move"), file=out)
            return 1
    return report(results, out, settings.collection_path)


def run_write(argv: list[str], out=sys.stdout) -> int:
    return run("write", argv, out)


def run_move(argv: list[str], out=sys.stdout) -> int:
    return run("move", argv, out)


def run_convert(argv: list[str], out=sys.stdout) -> int:
    return run("convert", argv, out)


def run_repair(argv: list[str], out=sys.stdout) -> int:
    return run("repair", argv, out)
