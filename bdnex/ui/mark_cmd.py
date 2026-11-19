"""``bdnex mark``: put your own marks on files or albums (``checked=keep``, ``read=yes``), and take them off."""

from __future__ import annotations

import argparse
import sys

from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui import context, describe
from bdnex.ui.output import add_pretend


def parse_marks(items: list[str]) -> list[tuple[str, str]]:
    """``["checked=keep", "read"]`` -> ``[("checked", "keep"), ("read", "")]``; the name is checked by the library."""
    marks = []
    for item in items:
        name, _, value = item.partition("=")
        marks.append((name.strip().lower(), value.strip()))
    return marks


def apply_marks(library, kind: str, target_ids: list[int], puts: list[tuple[str, str]], removes: list[str]) -> None:
    """Put and remove marks on files (``kind="file"``) or albums (``"album"``). Raises ``ValueError`` for a bad name."""
    for name, _ in puts:
        if not library.ATTR_NAME.match(name):
            raise ValueError(f"{name!r} is not a mark name: lower case letters, digits and _, starting with a letter.")
    for target_id in target_ids:
        for name, value in puts:
            library.set_attr(kind, target_id, name, value)
        for name in removes:
            library.remove_attr(kind, target_id, name)


def run(argv: list[str], out=sys.stdout, prompter=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex mark", description=__doc__,
        epilog="A mark is a name and a text value that bdnex keeps with a file (or an album, with --albums) and never "
               "changes by itself. Find them again with `attr:NAME` or `attr:NAME=VALUE` in any query, e.g. "
               "`bdnex ls --files attr:checked=keep`, or `bdnex candidates --low ^attr:checked` for the files nobody "
               "looked at. They are not written into the files.")
    parser.add_argument("query", nargs="+", help="which files, as in `bdnex ls --files` (e.g. series:nains)")
    parser.add_argument("--set", action="append", default=[], metavar="NAME[=VALUE]",
                        help="put this mark (lower case letters, digits and _); repeatable")
    parser.add_argument("--remove", action="append", default=[], metavar="NAME", help="take this mark off; repeatable")
    parser.add_argument("--albums", action="store_true", help="mark the albums the query matches, not the files")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    add_pretend(parser, "change")
    args = parser.parse_args(argv)
    if not args.set and not args.remove:
        parser.error("nothing to do: give --set NAME[=VALUE] or --remove NAME")
    settings = context.settings()
    if settings is None:
        return 1
    puts, removes = parse_marks(args.set), [name for name, _ in parse_marks(args.remove)]
    kind = "album" if args.albums else "file"
    with Library.for_settings(settings, readonly=args.pretend) as library:
        try:
            rows = library.query(args.query, files=not args.albums)
        except QueryError as err:
            return context.bad_query(err)
        targets = [(r["id"] if args.albums else r["file_id"], r) for r in rows if (r["id"] if args.albums else r["file_id"])]
        if not targets:
            print(f"No {kind} matches that query.", file=sys.stderr)
            return 1
        for name, value in puts:
            if not library.ATTR_NAME.match(name):
                print(f"{name!r} is not a mark name: lower case letters, digits and _, starting with a letter.", file=sys.stderr)
                return 2
        what = ", ".join([f"{n}={v}" if v else n for n, v in puts] + [f"remove {n}" for n in removes])
        from bdnex.ui import color

        on = color.enabled(out)
        print(f"{color.role(f'{len(targets)} {kind}(s)', 'count', on)}: {color.role(what, 'hint', on)}", file=out)
        for _, row in targets[:5]:
            print(f"  {color.role(row['path'] if not args.albums else describe.label(row), 'name', on)}", file=out)
        if len(targets) > 5:
            print(color.role(f"  ... and {len(targets) - 5} more", "note", on), file=out)
        if args.pretend:
            return 0
        if not args.yes and len(targets) > 1:
            if prompter is None:
                from bdnex.ui.prompts import new_prompter
                prompter = new_prompter()
            if not prompter.confirm(f"Change the marks of {len(targets)} {kind}(s)?"):
                print("Nothing changed.", file=out)
                return 1
        try:
            apply_marks(library, kind, [target_id for target_id, _ in targets], puts, removes)
        except ValueError as err:
            print(err, file=sys.stderr)
            return 2
    from bdnex.ui import color

    print(color.role("Done.", "good", color.enabled(out)), file=out)
    return 0
