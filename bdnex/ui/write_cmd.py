"""``bdnex write`` and ``bdnex move``: push library metadata into files."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from bdnex.config import ConfigError, load_config
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.organizer.organizer import COPY, FAILED, MOVE, WRITE, FileResult, Organizer, WriteAborted
from bdnex.ui import color
from bdnex.ui.output import add_verbosity, progress, setup_logging
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


def report(results, out, root: Optional[str] = None) -> int:
    on = color.enabled(out)
    for r in results:
        label = "would do" if r.outcome == "pretend" else r.outcome
        print(f"\n{color.paint('●', LABEL_STYLE.get(r.outcome, 'dim'), on)} "
              f"{color.paint(label, LABEL_STYLE.get(r.outcome, 'dim'), on)}  "
              f"{color.paint(os.path.basename(r.path), 'bold', on)}", file=out)
        if r.dest and r.dest != r.path:
            same, old, new = _changed_parts(_short(r.path, root), _short(r.dest, root))
            print(f"    from  {color.paint(same, 'dim', on)}{color.paint(old, 'red', on)}", file=out)
            print(f"    to    {color.paint(same, 'dim', on)}{color.paint(new, 'green', on)}", file=out)
        elif r.outcome == "pretend" and r.dest:
            print(f"    {color.paint('stays where it is', 'dim', on)}  {color.paint(_short(r.path, root), 'dim', on)}",
                  file=out)
        steps = _steps(r.note, r.dest) if r.outcome == "pretend" else [r.note] if r.note else []
        for step in steps:
            print(f"    {color.paint('•', 'dim', on)} {step}", file=out)
    summary = ", ".join(f"{sum(1 for r in results if r.outcome == o)} {o}" for o in OUTCOMES
                        if any(r.outcome == o for r in results))
    print(f"\n{color.rule()}\n{len(results)} file(s): {summary or 'nothing to do'}", file=out)
    if any(r.outcome == "pretend" for r in results):
        print("Preview only: nothing was changed. Run the same command without -n to do it.", file=out)
    return 1 if any(r.outcome == FAILED for r in results) else 0


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
            settings.require_library_directory()
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
            results = organizer.run(rows, mode, progress=progress)
    code = report(results, out, settings.directory)
    if organizer.aborted_at:
        print(aborted_message(organizer.aborted_at, command), file=out)
    return code


def place(settings, path: str, out=sys.stdout) -> int:
    """Move one registered file to where its album belongs (`bdnex move`, for a single file)."""
    try:
        settings.require_library_directory()
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
    return report(results, out, settings.directory)


def run_write(argv: list[str], out=sys.stdout) -> int:
    return run("write", argv, out)


def run_move(argv: list[str], out=sys.stdout) -> int:
    return run("move", argv, out)


def run_repair(argv: list[str], out=sys.stdout) -> int:
    return run("repair", argv, out)
