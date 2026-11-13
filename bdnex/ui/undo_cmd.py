"""``bdnex undo``: put back the files a `move` run renamed."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from bdnex.library import Library
from bdnex.organizer.organizer import Organizer
from bdnex.ui import color, context, results
from bdnex.ui.output import add_pretend


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex undo", description=__doc__,
        epilog="Every `bdnex move` (and `import --move`) run remembers the files it renamed or moved. `bdnex undo` lists "
               "the runs; `bdnex undo last` or `bdnex undo <run>` moves its files back to where they were, in the "
               "library too. What cannot be taken back: tags written into a file (the file keeps them), a CBR or PDF "
               "that was converted (the original is in the trash: `bdnex trash`), copies, and files that were changed "
               "or moved again since.")
    parser.add_argument("batch", nargs="?", help="the run to take back: its name from the list, or `last`")
    add_pretend(parser, "be moved back")
    args = parser.parse_args(argv)
    settings = context.settings()
    if settings is None:
        return 1

    on = color.enabled(out)
    with Library.for_settings(settings, readonly=args.batch is None or args.pretend) as library:
        batches = library.batches()
        if args.batch is None:
            if not batches:
                print("No run has moved files yet.", file=out)
                return 0
            for batch in batches:
                left = batch["moves"] - batch["undone"]
                print(f"{color.paint(batch['batch'], 'bold', on)}  {batch['moves']} file(s) moved"
                      + (f", {batch['undone']} taken back" if batch["undone"] else "")
                      + ("" if left else color.paint("  (nothing left to undo)", "dim", on)), file=out)
            print("\n`bdnex undo last` (or `bdnex undo <run>`) moves a run's files back; add -n to preview.", file=out)
            return 0
        name = batches[0]["batch"] if args.batch == "last" and batches else args.batch
        if name not in {b["batch"] for b in batches}:
            print(f"No such run: {args.batch} (`bdnex undo` lists them).", file=sys.stderr)
            return 1
        organizer = Organizer(library, settings)
        counts = {"back": 0, "skipped": 0}
        for move in library.moves_of(name):
            old, new = move["old_path"], move["new_path"]
            problem = (f"{new} is gone" if not os.path.exists(new) else
                       f"{old} is taken by another file" if os.path.exists(old) else None)
            if problem:
                counts["skipped"] += 1
                print(results.block("skipped", os.path.basename(new), [problem], on), file=out)
                continue
            counts["back"] += 1
            print(results.block("would move back" if args.pretend else "back", os.path.basename(new),
                                [f"from  {color.paint(new, 'red', on)}", f"to    {color.paint(old, 'green', on)}"], on), file=out)
            if args.pretend:
                continue
            os.makedirs(os.path.dirname(old), exist_ok=True)
            shutil.move(new, old)
            library.move_file(new, old)
            library.mark_undone(move["id"])
            organizer._prune_empty_dirs(new)
        verb = "would be moved back" if args.pretend else "moved back"
        print(f"\n{counts['back']} file(s) {verb}, {counts['skipped']} skipped.", file=out)
        return 0 if not counts["skipped"] else 1
