"""The commands of ``bdnex``: one table that the usage text, the dispatch and the tab completion all read."""

from __future__ import annotations

import importlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    module: str  # in ``bdnex.ui``; its ``run(argv)`` is the command
    summary: str
    needs_config: bool = True  # False: works before a configuration exists
    by_name: bool = False  # ``run(name, argv)``: one module serves several commands (write, move, repair, convert)
    by_function: str = ""  # the function of the module to call instead of ``run`` (``fields_run``)


GROUPS: tuple[tuple[str, tuple[Command, ...]], ...] = (
    ("Import and match", (
        Command("init", "init_cmd", "download or refresh the album list that file names are matched against"),
        Command("import", "import_cmd", "match comic files to Bédéthèque albums and record them in the library"),
        Command("candidates", "candidates_cmd",
                "for files whose match is in doubt: the other albums they could be, with the evidence"),
        Command("review", "review_cmd", "go through the doubtful matches: one by one with both covers, or settled by rule with --auto"),
    )),
    ("Look at the library", (
        Command("ls", "ls_cmd", "list and search the library"),
        Command("series", "series_cmd", "what you have of each series, and what is missing"),
        Command("stats", "stats_cmd", "how much the library holds: albums read or only matched, files, formats, sizes"),
        Command("fields", "stats_cmd", "the fields a query can use, with their kind", by_function="fields_run"),
        Command("duplicates", "duplicates_cmd", "the same book in several files"),
    )),
    ("Change the library", (
        Command("edit", "edit_cmd", "change the metadata of albums or series by hand (kept across re-imports)"),
        Command("mark", "mark_cmd", "put your own marks on files or albums (checked=keep), to find them again with attr:"),
        Command("reset", "reset_cmd", "empty the library (albums, files, edits), keep the downloaded sitemaps and pages"),
        Command("update", "update_cmd", "read the files again after another tool changed them (the opposite of write)"),
        Command("check", "check_cmd",
                "is the library consistent with itself and the disk? (--matches: do the matches look right?)"),
    )),
    ("Change the files", (
        Command("write", "write_cmd", "write ComicInfo.xml into files (converts CBR/PDF to CBZ)", by_name=True),
        Command("move", "write_cmd", "write tags and rename files into the library tree (--copy keeps the originals)",
                by_name=True),
        Command("repair", "write_cmd", "rebuild damaged archives (wrong page checksums) as clean CBZ files", by_name=True),
        Command("convert", "write_cmd", "convert the PDF files of the library to CBZ (next to the original)", by_name=True),
        Command("undo", "undo_cmd", "put back the files a `move` run renamed"),
        Command("trash", "trash_cmd", "list, restore or empty the files that were replaced"),
    )),
    ("Files that moved", (
        Command("relocate", "relocate_cmd", "your whole comics folder moved: tell the library the new place"),
    )),
    ("Other programs", (
        Command("komga", "komga_cmd", "see what a Komga server holds for your files (show), or send it the library's metadata (push)"),
        Command("serve", "serve_cmd",
                "open the library in a browser (web page and JSON API; read-only unless you allow changes)", needs_config=False),
    )),
    ("Setup", (
        Command("config", "config_cmd", "show or edit the configuration", needs_config=False),
        Command("aliases", "aliases_cmd", "list your short commands, add one (--add) or remove one (--rm) in the configuration file"),
        Command("completion", "completion_cmd", "print a tab-completion script for bash, zsh or fish", needs_config=False),
    )),
)

COMMANDS: dict[str, Command] = {command.name: command for _, group in GROUPS for command in group}


def _reparse(argv: list[str]) -> list[str]:
    """``bdnex reparse -n series:x`` is ``bdnex import --reparse -n --select series:x`` (its terms name albums)."""
    new, takes_value = ["import", "--reparse"], False
    for arg in argv:
        if takes_value or arg.startswith("-"):
            new.append(arg)
            takes_value = arg == "--log-file"
        else:
            new += ["--select", arg]
    return new


# Commands that were folded into another: the old name still works, with a note, for a release.
RENAMED: dict = {"recheck": lambda argv: ["review", "--auto", *argv], "reparse": _reparse,
                 "relink": lambda argv: ["relocate", "--find", *argv]}


def parse_alias(text: str) -> tuple[str, list[str], str]:
    """``"fix-flagged-matches = review --auto  # settle the flagged files by rule"`` -> (name, command words, explanation).

    Raises ``ValueError`` with the reason when it is not an alias."""
    import re
    import shlex

    body, _, why = text.partition(" # ")
    name, sep, rest = body.partition("=")
    name = name.strip()
    if not sep or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise ValueError(f"alias {text!r}: write it as 'NAME = COMMAND OPTIONS  # WHY' (NAME in lower case, "
                         "letters, digits and dashes; WHY is what it is for)")
    if name in COMMANDS or name in RENAMED:
        raise ValueError(f"alias {text!r}: {name!r} is already a command")
    try:
        words = shlex.split(rest)
    except ValueError as err:
        raise ValueError(f"alias {text!r}: {err}") from err
    if not words or words[0] not in COMMANDS:
        raise ValueError(f"alias {text!r}: {words[0] if words else 'nothing'!r} is not a bdnex command")
    return name, words, why.strip()


def expand_alias(argv: list[str], aliases: list[str]) -> list[str]:
    """``bdnex fix -n`` with the alias ``fix = review --auto`` is ``bdnex review --auto -n``. Not an alias: ``argv`` as it is."""
    for text in aliases:
        try:
            name, words, _ = parse_alias(text)
        except ValueError:
            continue
        if argv and name == argv[0]:
            return [*words, *argv[1:]]
    return argv


def alias_lines(aliases: list[str]) -> list[str]:
    """The ``Your aliases`` part of the usage text: each name, what it runs, and what it is for."""
    parsed = []
    for text in aliases:
        try:
            parsed.append(parse_alias(text))
        except ValueError:
            continue
    width = max((len(name) for name, _, _ in parsed), default=0)
    lines = [f"  {name:<{width}}  bdnex {' '.join(words)}" + (f"   # {why}" if why else "") for name, words, why in parsed]
    return ["Your aliases (`aliases` in the configuration):", *lines, ""] if lines else []


def usage() -> str:
    lines = ["usage: bdnex <command> [options]", ""]
    for title, group in GROUPS:
        lines.append(f"{title}:")
        lines.extend(f"  {command.name:<11} {command.summary}" for command in group)
        lines.append("")
    lines += ["Run `bdnex <command> --help` for the options of a command.",
              "The former `bdnex -f FILE` / `-d DIR` still work and run `bdnex import -W`.", ""]
    return "\n".join(lines)


def runner(command: Command):
    """``run(argv) -> int`` of a command (its module is imported only when it is used: starting stays quick).

    ``--help`` is followed by the command's examples (``ui/examples.py``), so no command is without one."""
    module = importlib.import_module(f"bdnex.ui.{command.module}")
    run = getattr(module, command.by_function) if command.by_function else module.run
    call = (lambda argv: run(command.name, argv)) if command.by_name else run

    def with_examples(argv):
        try:
            return call(argv)
        except SystemExit as stop:
            if stop.code in (0, None) and any(arg in ("-h", "--help") for arg in argv):
                from bdnex.ui.examples import text

                print(text(command.name))
            raise

    return with_examples
