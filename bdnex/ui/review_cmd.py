"""``bdnex review``: look at the matches whose cover looked different, one by one."""

from __future__ import annotations

import argparse
import sys

from bdnex.importer import covers
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui.describe import label
from bdnex.ui import context


def run(argv: list[str], out=sys.stdout, prompter=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex review", description=__doc__,
        epilog="`bdnex import` flags a file when its cover is not like its album's cover (similarity below "
               "`cover.match_percentage`) and nobody was there to look at it. Each flagged file is shown with both "
               "covers side by side (the file's on the left, the album's on the right) and you keep the match, "
               "unlink the file, ask for another album, or leave it for later.")
    parser.add_argument("query", nargs="*", help="only these files, same terms as `bdnex ls --files` (e.g. series:nains)")
    parser.add_argument("-l", "--list", action="store_true", help="only list the flagged files and why; ask nothing")
    args = parser.parse_args(argv)
    settings = context.settings()
    if settings is None:
        return 1

    rematch: list[str] = []
    counts = {"keep": 0, "unlink": 0, "rematch": 0, "skip": 0}
    aborted = False
    with Library.for_settings(settings, readonly=args.list) as library:
        try:
            rows = library.query(["review:true", *args.query], files=True)
        except QueryError as err:
            return context.bad_query(err)
        if not rows:
            print("Nothing to review.", file=out)
            return 0
        if args.list:
            for row in rows:
                print(f"{row['path']}\n    {label(row)}\n    {row['review']}", file=out)
            print(f"\n{len(rows)} file(s) wait for a review (`bdnex review` goes through them).", file=out)
            return 0
        if prompter is None:
            from bdnex.ui.prompts import new_prompter

            prompter = new_prompter(thumbnails=settings.import_.thumbnails)
        from bdnex.importer.session import ImportAborted, NeedsTerminal

        try:
            for number, row in enumerate(rows, start=1):
                album = library.get_album(row["id"]) if row["id"] is not None else None  # a file query row: `id` is the album
                if album is None:
                    library.clear_review(row["path"])
                    continue
                kept = album.get("cover_file")
                album_cover = (settings.cover_path / kept).read_bytes() if kept and (settings.cover_path / kept).exists() else None
                file_cover = covers.front_cover(row["path"], row["format"])
                answer = prompter.review_file(f"[{number}/{len(rows)}]", row["path"], label(album), row["review"],
                                              file_cover, album_cover)
                counts[answer] += 1
                if answer == "keep":
                    library.clear_review(row["path"])
                elif answer == "unlink":
                    library.set_file_album(row["path"], None)
                    library.log_import(row["path"], "skipped", row.get("cover_score"))
                elif answer == "rematch":
                    rematch.append(row["path"])
        except NeedsTerminal:
            print("This has to ask you about each file, and there is no terminal. `bdnex review --list` lists them.",
                  file=sys.stderr)
            return 2
        except ImportAborted:
            aborted = True
    print(f"\nKept {counts['keep']}, unlinked {counts['unlink']}, to match again {counts['rematch']}, "
          f"left for later {counts['skip']}." + (" Aborted: the rest are still flagged." if aborted else ""), file=out)
    if rematch and not aborted:
        from bdnex.ui import import_cmd

        print("\nMatching again the files you chose:", file=out)
        return import_cmd.run([*rematch, "--match", "--force"], out)
    return 0
