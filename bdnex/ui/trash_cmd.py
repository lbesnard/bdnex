"""``bdnex trash``: look at, restore or empty the files that were replaced."""

from __future__ import annotations

import argparse
import sys

from bdnex import trash
from bdnex.library import Library
from bdnex.formatting import human_size
from bdnex.ui import context
from bdnex.ui.output import add_pretend


def run(argv: list[str], out=sys.stdout, prompter=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex trash", description=__doc__,
        epilog="When you choose to keep a new file instead of an existing one, the existing file is moved "
               "here (to `trash.dir` in your library folder), never deleted. Emptying the trash deletes the files "
               "and their records for good.")
    parser.add_argument("--restore", type=int, metavar="ID", help="put that file back where it came from")
    after = parser.add_mutually_exclusive_group()
    after.add_argument("--place", action="store_true",
                       help="with --restore: then move it to where its album belongs (as `bdnex move` does)")
    after.add_argument("--reimport", action="store_true",
                       help="with --restore: then match it again (you choose the album, the old link is replaced) "
                            "and move it into the library tree, as `bdnex import --match --force --move` does")
    parser.add_argument("--empty", action="store_true", help="delete the trashed files for good")
    parser.add_argument("--older-than", type=int, metavar="DAYS", help="with --empty: only what was trashed that long ago")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    add_pretend(parser)
    args = parser.parse_args(argv)
    if (args.place or args.reimport) and args.restore is None:
        parser.error("--place and --reimport go with --restore ID")
    settings = context.settings()
    if settings is None:
        return 1

    if args.restore is not None:
        return _restore(args, settings, out)

    with Library.for_settings(settings) as library:
        entries = library.trash_entries(args.older_than if args.empty else None)
        if not args.empty:
            for entry in entries:
                print(f"{entry['id']:>4}  {entry['trashed'][:16]}  {human_size(entry['size'] or 0):>9}  {entry['path']}", file=out)
                print(f"      {entry['reason']}", file=out)
            where = settings.trash_path
            total = sum(e["size"] or 0 for e in entries)
            print(f"\n{len(entries)} file(s), {human_size(total)} in {where}" if entries
                  else f"The trash is empty ({where}).", file=out)
            if entries:
                print("`bdnex trash --restore ID` puts one back; `bdnex trash --empty` deletes them for good.", file=out)
            return 0

        if not entries:
            print("Nothing to empty.", file=out)
            return 0
        total = sum(e["size"] or 0 for e in entries)
        print(f"{len(entries)} file(s), {human_size(total)}" + (f", trashed more than {args.older_than} days ago"
                                                           if args.older_than is not None else ""), file=out)
        if args.pretend:
            return 0
        if not args.yes:
            if prompter is None:
                from bdnex.ui.prompts import new_prompter
                prompter = new_prompter()
            if not prompter.confirm("Delete them for good?"):
                print("Nothing changed.", file=out)
                return 1
        count, freed = trash.empty(library, entries)
        print(f"Deleted {count} file(s), freed {human_size(freed)}.", file=out)
    return 0


def _restore(args, settings, out) -> int:
    with Library.for_settings(settings) as library:
        entry = library.get_trash(args.restore)
        if entry is None:
            print(f"No trash entry {args.restore} (`bdnex trash` lists them).", file=sys.stderr)
            return 1
        if args.pretend:
            print(f"Would restore {entry['path']}", file=out)
            return 0
        restored = trash.restore(library, args.restore)
    if restored is None:
        print("That file is no longer in the trash folder; its record was removed.", file=sys.stderr)
        return 1
    print(f"Restored {restored}", file=out)
    # the library is closed again: the follow-up commands open it themselves
    if args.reimport:
        from bdnex.ui import import_cmd

        return import_cmd.run([restored, "--match", "--force", "--move"], out)
    if args.place:
        from bdnex.ui import write_cmd

        return write_cmd.place(settings, restored, out)
    return 0
