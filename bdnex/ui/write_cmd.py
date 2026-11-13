"""``bdnex write`` and ``bdnex move``: push library metadata into files."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from bdnex.config import ConfigError, load_config
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.organizer.organizer import auto_workers, COPY, FAILED, MOVE, WRITE, FileResult, Organizer, WriteAborted
from bdnex.ui import color
from bdnex.ui.output import Bar, add_verbosity, banner, progress, setup_logging
from bdnex.ui.prompts import InquirerPrompter

OUTCOMES = ("repaired", "written", "moved", "copied", "unchanged", "skipped", "failed", "pretend")


def _parser(command: str) -> argparse.ArgumentParser:
    if command == "repair":
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
    parser.add_argument("-n", "--pretend", action="store_true", help="show what would be done; change nothing")
    parser.add_argument("--workers", type=int, metavar="N",
                        help="files written at once (0: the cores less one, at most 4; 1: one by one); default: `write.workers`")
    parser.add_argument("--force", action="store_true",
                        help="never ask: the database wins, name clashes get a number, originals are kept")
    if command == "move":
        parser.add_argument("--copy", action="store_true",
                            help="leave the source files untouched; the copy is what the database tracks")
    add_verbosity(parser)
    return parser


LABEL_STYLE = {"repaired": "green_bold", "written": "green_bold", "moved": "green_bold", "copied": "green_bold",
               "unchanged": "dim", "skipped": "yellow", "failed": "red_bold", "pretend": "cyan_bold"}


def _short(path: str, root: Optional[str]) -> str:
    """The path below the library folder when it is in it (the rest of the line is the same for every file)."""
    if root:
        root = os.path.expanduser(root).rstrip("/") + "/"
        if path.startswith(root):
            return path[len(root):]
    return path


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
    style = LABEL_STYLE.get(r.outcome, "dim")
    counter = f"{color.paint(f'[{number}/{total}]', 'dim', on)} " if number else ""
    lines = [f"\n{counter}{color.paint('●', style, on)} {color.paint(label, style, on)}  "
             f"{color.paint(os.path.basename(r.path), 'bold', on)}"]
    if r.dest and r.dest != r.path:
        same, old, new = _changed_parts(_short(r.path, root), _short(r.dest, root))
        lines.append(f"    from  {color.paint(same, 'dim', on)}{color.paint(old, 'red', on)}")
        lines.append(f"    to    {color.paint(same, 'dim', on)}{color.paint(new, 'green', on)}")
    elif r.outcome == "pretend" and r.dest:
        lines.append(f"    {color.paint('stays where it is', 'dim', on)}  {color.paint(_short(r.path, root), 'dim', on)}")
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


LEGEND = ("Each file: its archive is read, a new CBZ is written with the ComicInfo.xml and the pages byte for byte, the "
          "new file is checked against the original pages, then put in place (the original goes only after that).")


def _run_files(organizer, rows, mode, out, root, command: str, workers: int = 1) -> list:
    """Process the files showing what happens: a bar with the file and the step on a terminal, and each result as soon
    as it is known (not all of them at the end)."""
    on = color.enabled(out)
    bar = Bar(len(rows), "Files") if rows and sys.stderr.isatty() and not organizer.pretend else None
    if rows and not organizer.pretend:
        banner(f"{'Writing tags into' if mode == WRITE else 'Copying' if mode == COPY else 'Moving'} {len(rows)} file(s)")
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
        return organizer.run(rows, mode, progress=starting, on_result=done, workers=workers, on_busy=busy)
    finally:
        if bar:
            bar.finish()


def aborted_message(path: str, command: str = "write") -> str:
    return (f"Aborted at {path}. What was done is kept and the other files were not touched: "
            f"run `bdnex {command}` again to continue (finished files are reported as unchanged).")


def run(command: str, argv: list[str], out=sys.stdout) -> int:
    args = _parser(command).parse_args(argv)
    setup_logging(args.verbose)
    mode = WRITE if command in ("write", "repair") else (COPY if args.copy else MOVE)
    try:
        settings = load_config().settings
        if mode != WRITE:
            settings.require_collection()
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    if not settings.library_path.exists():
        print(f"No library yet ({settings.library_path}): run `bdnex import <folder>` first.", file=sys.stderr)
        return 1

    if args.pretend:
        print("Preview: nothing is written, moved, converted or deleted, and the library is opened read-only.",
              file=sys.stderr)
    with Library.for_settings(settings, readonly=args.pretend) as library:
        try:
            rows = library.query([*args.query, "corrupted:true"] if command == "repair"
                                 else [*args.query, "tagged:true"], files=True)
        except QueryError as err:
            print(f"bad query: {err}", file=sys.stderr)
            return 2
        organizer = Organizer(library, settings, prompter=None if args.force else InquirerPrompter(),
                              pretend=args.pretend, force=args.force)
        if command == "repair":
            results = []
            for number, row in enumerate(rows, start=1):
                progress(f"[{number}/{len(rows)}] {os.path.basename(row['path'])}")
                try:
                    results.append(organizer.repair(row))
                except WriteAborted:
                    organizer.aborted_at = row["path"]
                    break
                except Exception as err:
                    results.append(FileResult(row["path"], FAILED, note=str(err)))
        else:
            workers = args.workers if args.workers is not None else settings.write.workers
            results = _run_files(organizer, rows, mode, out, settings.collection_path, command,
                                 workers or auto_workers())
    code = summary(results, out) if command != "repair" else report(results, out, settings.collection_path)
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
        organizer = Organizer(library, settings, prompter=InquirerPrompter())
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


def run_repair(argv: list[str], out=sys.stdout) -> int:
    return run("repair", argv, out)
