"""``bdnex review``: go through the flagged matches one by one, with the file's cover and the album's side by side."""

from __future__ import annotations

import argparse
import sys

from bdnex.importer import covers
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui.describe import label
from bdnex.ui import context
from bdnex.ui.output import add_pretend, add_verbosity, setup_logging


def settle(library, row: dict, answer: str) -> None:
    """Apply a ``keep`` or ``unlink`` answer to a flagged file: the one place, for the terminal and the web page."""
    if answer == "keep":
        library.clear_review(row["path"])
    elif answer == "unlink":
        library.set_file_album(row["path"], None)
        library.log_import(row["path"], "skipped", row.get("cover_score"))


def add_candidate_options(parser) -> None:
    from bdnex.ui.verdicts import add_candidate_options as add

    add(parser)


def _auto(args, settings, out, prompter) -> int:
    """``--auto``: weigh every flagged file and every match applied under the strong threshold, and settle them by rule."""
    import json
    from collections import Counter

    from bdnex.library.recheck import KEPT, UNCONFIRMED, WRONG, weak_matches
    from bdnex.ui import verdicts as rules

    if not context.has_library(settings, "nothing to review."):
        return 1
    with Library.for_settings(settings, readonly=args.pretend) as library:
        try:
            found = weak_matches(library, settings, args.query, compare=not args.no_cover, store=not args.pretend,
                                 flagged=True, weak=True)
        except QueryError as err:
            return context.bad_query(err)
        if not args.no_cover:
            try:
                rules.look_for_better(settings, library, found, args.candidates,
                                      say=lambda text: print(text, file=sys.stderr, flush=True))
            except KeyboardInterrupt:
                print("Interrupted: the covers of the other candidates are not all compared.", file=sys.stderr)
        count = Counter(v.kind for v in found)
        if args.json:
            print(json.dumps([v.as_dict() for v in found], ensure_ascii=False, indent=2), file=out)
            return 0
        better = sum(1 for v in found if v.better)
        print(f"{len(found)} file(s) weighed (flagged, or matched under {settings.match.strong_threshold:.0f}): "
              f"{count[KEPT]} confirmed, {count[WRONG]} contradicted ({better} switched to a better album, "
              f"{count[WRONG] - better} unlinked), {count[UNCONFIRMED]} unconfirmed (left as they are).", file=out)
        why = Counter(v.note or "no evidence either way" for v in found if v.kind == UNCONFIRMED)
        hints = {"page not read": "the album page was never read: `bdnex import --fetch --select partial:true`",
                 "cover not compared": "no cover to compare (the file, or the album's cover, could not be read)",
                 "no evidence either way": "the page and the cover say nothing for or against"}
        for reason, number in why.most_common():
            print(f"    {number} unconfirmed: {hints[reason]}", file=out)
        if args.pretend:
            return 0
        try:
            done = rules.settle_verdicts(library, found, yes=args.yes, prompter=prompter, out=out)
        except rules.Declined:
            return 1
    print(f"Done: {done['cleared']} flag(s) taken off, {done['switched']} switched, {done['unlinked']} unlinked"
          + (f", {done['refused']} could not be switched (left flagged)" if done["refused"] else "") + "."
          + (" `bdnex import --skipped --auto=all` decides the unlinked ones again." if done["unlinked"] else ""), file=out)
    return 0


def run(argv: list[str], out=sys.stdout, prompter=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex review", description=__doc__,
        epilog="A match is flagged when its cover is not like its album's cover (similarity below `cover.match_percentage`) "
               "or the album's page contradicts the file (year, volume, the file's own tags, its folder: see `bdnex check "
               "--matches`), and nobody was there to look at it. Each flagged file is shown with both covers side by side "
               "(the file's on the left, the album's on the right) and you keep the match, unlink the file, ask for "
               "another album, or leave it for later. `bdnex candidates` lists the other albums a file could be.")
    parser.add_argument("query", nargs="*", help="only these files, same terms as `bdnex ls --files` (e.g. series:nains)")
    parser.add_argument("--list", action="store_true", help="only list the flagged files and why; ask nothing")
    parser.add_argument("--scan", action="store_true", help=argparse.SUPPRESS)  # now `bdnex check --matches --fix`
    parser.add_argument("--json", action="store_true",
                        help="with --list: the flagged files as JSON, with the evidence; with --auto: the verdicts as JSON (nothing "
                             "is changed), for a script or an agent")
    parser.add_argument("--auto", action="store_true",
                        help="settle by rule, asking nothing, every flagged file and every match applied under "
                             "`match.strong_threshold`: the ones confirmed by something other than the name lose their "
                             "flag; the ones something contradicts are switched to another album whose cover is clearly "
                             "the file's (see --candidates), else unlinked (`bdnex import --skipped --auto=all` decides "
                             "them again); the ones nothing confirms or contradicts are left as they are")
    add_candidate_options(parser)
    parser.add_argument("-y", "--yes", action="store_true", help="with --auto: do not ask for confirmation before unlinking or switching")
    add_pretend(parser, "be settled (with --auto)")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    settings = context.settings()
    if settings is None:
        return 1
    if args.auto:
        return _auto(args, settings, out, prompter)
    if args.scan:  # the former spelling, kept for a release
        print("`bdnex review --scan` is now `bdnex check --matches --fix`.", file=sys.stderr)
        from bdnex.ui import check_cmd

        if check_cmd.run(["--matches", "--fix"], out) > 1:
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
        if args.list and args.json:
            import json

            print(json.dumps([{"path": row["path"], "album": label(row), "album_url": row.get("url"), "why": row["review"]}
                              for row in rows], ensure_ascii=False, indent=2), file=out)
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
                if answer == "rematch":
                    rematch.append(row["path"])
                else:
                    settle(library, row, answer)
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

