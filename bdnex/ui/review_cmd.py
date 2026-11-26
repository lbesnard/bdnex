"""``bdnex review``: the review screen (folders, filters, the doubtful matches, duplicates, bulk edits); ``--no-tui`` asks one file at a time."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

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


def saved_place(library) -> Optional[dict]:
    """Where the review screen was closed (what it showed, the sort, the find boxes, the ticks): ``None`` when it never saved one."""
    import json

    try:
        place = (json.loads(library.get_meta("review.view") or "{}") or {}).get("last")
    except Exception:
        return None
    return place if isinstance(place, dict) and place else None


def _batch(args, settings, out) -> int:
    """``--batch N``: the table (Textual) over the next N doubtful files; every decision is saved as it is made."""
    import sys

    try:
        from bdnex.ui import review_tui
    except ImportError:
        print("`bdnex review --batch` needs Textual (installed with bdnex): `pip install textual`. "
              "`bdnex review` and `bdnex review --auto` work without it.", file=sys.stderr)
        return 2
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("`bdnex review --batch` is a screen: run it in a terminal (`bdnex review --list` lists the files).", file=sys.stderr)
        return 2
    if args.batch < 1:
        print("--batch takes a number of files, at least 1.", file=sys.stderr)
        return 2
    from bdnex.importer.registry import create_source
    from bdnex.ui import color
    from bdnex.importer.registry import offline_settings

    if not context.has_library(settings, "nothing to review."):
        return 1
    offline = offline_settings(settings)  # searching the list needs no request

    def build_source():
        """In the thread of the album list: its own read-only library. The album list is loaded later (`SourceHub.load_list`, once the table
        is on screen)."""
        own = Library.for_settings(settings, readonly=True)
        return create_source(settings.source, own, offline), own.close

    with Library.for_settings(settings) as library:
        try:
            if not create_source(settings.source, library, offline).is_ready():
                print("The album list is not downloaded yet: run `bdnex init` first.", file=sys.stderr)
                return 1
            if args.query:  # a bad query is said now, not from inside the screen (no query: nothing to check, and the check reads the library)
                library.query(args.query, files=True, limit=1)
        except QueryError as err:
            return context.bad_query(err)
        except Exception as err:
            print(err, file=sys.stderr)
            return 1
        place = saved_place(library) if args.resume else None
        if args.resume and not place:
            print("Nothing to continue yet (the screen saves where you are as you work): starting as usual.", file=sys.stderr)
        if place and not (args.query or args.unmatched or args.all or args.again):  # what you type now wins over what was saved
            args.query, args.unmatched = list(place.get("terms") or []), bool(place.get("unmatched"))
            args.all, args.again = bool(place.get("everything")), bool(place.get("again"))
        decided = review_tui.run(library, settings, build_source, args.batch, args.query, args.unmatched, args.all, args.again, args.verbose,
                                  False if args.no_tree else None, place)
    on = color.enabled(out)
    total = sum(decided.values())
    parts = ", ".join(f"{n} {what}" for what, n in sorted(decided.items()) if n)
    print(f"{color.role(str(total), 'count', on)} file(s) checked" + (f" ({parts})" if parts else "") + ".", file=out)
    left = getattr(review_tui.run, "unfetched", 0)
    if left:
        print(color.role(f"{left} linked file(s) have no page or cover yet", "warn", on) + color.hints(": `bdnex import --fetch` gets them.", on), file=out)
    print(color.hints("Run `bdnex review --batch " + str(args.batch) + "` again to continue; "
                      "`bdnex import --skipped --auto=all` decides the unlinked ones again.", on), file=out)
    return 0


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
        if not args.no_cover:  # (it may download the covers of other candidates into the cover folder, also in a preview: that is how the preview
            # says which files would be switched to a better album; the library is not changed. `--no-cover` skips it.)
            try:
                rules.look_for_better(settings, library, found, args.candidates,
                                      say=lambda text: print(text, file=sys.stderr, flush=True))
            except KeyboardInterrupt:
                print("Interrupted: the covers of the other candidates are not all compared.", file=sys.stderr)
        count = Counter(v.kind for v in found)
        if args.json:
            print(json.dumps([v.as_dict() for v in found], ensure_ascii=False, indent=2), file=out)
            return 0
        from bdnex.ui import color

        on = color.enabled(out)
        better = sum(1 for v in found if v.better)
        print(f"{color.role(str(len(found)), 'count', on)} file(s) weighed (flagged, or matched under "
              f"{settings.match.strong_threshold:.0f}): "
              f"{color.verdict(f'{count[KEPT]} confirmed', 'confirmed', on)}, "
              f"{color.verdict(f'{count[WRONG]} contradicted', 'contradicted', on)} "
              f"({color.role(f'{better} switched to a better album', 'good', on)}, "
              f"{color.role(f'{count[WRONG] - better} unlinked', 'bad', on)}), "
              f"{color.verdict(f'{count[UNCONFIRMED]} unconfirmed', 'unconfirmed', on)} (left as they are).", file=out)
        why = Counter(v.note or "no evidence either way" for v in found if v.kind == UNCONFIRMED)
        hints = {"page not read": "the album page was never read: `bdnex import --fetch --select partial:true`",
                 "cover not compared": "no cover to compare (the file, or the album's cover, could not be read)",
                 "no evidence either way": "the page and the cover say nothing for or against"}
        for reason, number in why.most_common():
            print(f"    {color.role(f'{number} unconfirmed', 'warn', on)}: {color.hints(hints[reason], on)}", file=out)
        if args.pretend:
            return 0
        try:
            done = rules.settle_verdicts(library, found, yes=args.yes, prompter=prompter, out=out)
        except rules.Declined:
            return 1
    from bdnex.ui import color

    on = color.enabled(out)
    print(f"{color.role('Done', 'good', on)}: {done['cleared']} flag(s) taken off, "
          f"{color.role(str(done['switched']) + ' switched', 'good', on)}, "
          f"{color.role(str(done['unlinked']) + ' unlinked', 'bad', on)}"
          + (", " + color.role(f"{done['refused']} could not be switched (left flagged)", "warn", on) if done["refused"] else "") + "."
          + color.hints(" `bdnex import --skipped --auto=all` decides the unlinked ones again." if done["unlinked"] else "", on),
          file=out)
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
    parser.add_argument("--no-tui", action="store_true",
                        help="do not open the review screen: ask about the flagged files one at a time, in the terminal, with both covers "
                             "side by side (the screen is the default in a terminal)")
    parser.add_argument("--batch", type=int, metavar="N",
                        help="the size of a batch in the review screen (default 50): the next N files to check: file, folder, album now, "
                             "match score, cover score; keep, unlink or pick another album from the album list. Every decision "
                             "is saved at once and the files checked are not shown again: run it again to continue; a query "
                             "limits the files, as in `bdnex ls --files`")
    parser.add_argument("--continue", dest="resume", action="store_true",
                        help="open the review screen where you left it: the folder or filters you were looking at, the page, the sort, the find boxes, "
                             "the ticks and the query (the columns and panes are always kept). Options you type now win over the saved ones")
    parser.add_argument("--unmatched", action="store_true", help="in the screen: also the files that have no album")
    parser.add_argument("--no-tree", action="store_true",
                        help="in the screen: start with the left panel hidden (`f` shows it; the screen remembers how you left it)")
    parser.add_argument("--all", action="store_true",
                        help="in the screen: every file of the query, not only the doubtful ones (those come first, then the files "
                             "without album, then the rest): to look through a series, e.g. `--batch 50 series:batman --all`")
    parser.add_argument("--again", action="store_true",
                        help="in the screen: also the files already checked (the table says what you decided then)")
    add_candidate_options(parser)
    parser.add_argument("-y", "--yes", action="store_true", help="with --auto: do not ask for confirmation before unlinking or switching")
    add_pretend(parser, "be settled (with --auto)")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    settings = context.settings()
    if settings is None:
        return 1
    asked = args.unmatched or args.all or args.again or args.no_tree
    screen = args.batch is not None or args.resume or (not (args.list or args.auto or args.scan or args.no_tui or args.json) and prompter is None
                                        and sys.stdin.isatty() and sys.stdout.isatty())  # in a terminal, `bdnex review` is the screen
    if args.no_tui and (args.batch is not None or asked or args.resume):
        print("--no-tui asks one file at a time: --batch, --continue, --unmatched, --all, --again and --no-tree are for the screen.", file=sys.stderr)
        return 2
    if asked and not screen:
        print("--unmatched, --all, --again and --no-tree are for the review screen (it needs a terminal).", file=sys.stderr)
        return 2
    if screen:  # (`--batch 0` is refused by the screen's own check: it used to be read as 'no batch' and start --auto)
        if args.batch is None:
            args.batch = 50
        return _batch(args, settings, out)
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
            from bdnex.ui import color

            on = color.enabled(out)
            for row in rows:
                print(f"{color.role(row['path'], 'path', on)}\n    {color.role(label(row), 'name', on)}\n"
                      f"    {color.role(str(row['review']), 'warn', on)}", file=out)
            print(color.hints(f"\n{len(rows)} file(s) wait for a review (`bdnex review` goes through them).", on), file=out)
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

