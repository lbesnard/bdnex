"""``bdnex recheck``: the matches applied below the strong threshold, weighed again with what the library knows."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.library.recheck import KEPT, UNCONFIRMED, WRONG, weak_matches
from bdnex.ui import color, context
from bdnex.ui.output import add_pretend, add_verbosity, setup_logging
from bdnex.ui.review_cmd import settle

MEANING = {KEPT: "confirmed by something other than the name", WRONG: "something contradicts it",
           UNCONFIRMED: "nothing confirms it"}


def run(argv: list[str], out=sys.stdout, prompter=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex recheck", description=__doc__,
        epilog="For each file linked with a score under `match.strong_threshold` (a medium match, applied before "
               "`import --auto=all` became strict): its album's page, the file's own tags and its cover against the album's "
               "saved cover. Confirmed ones are left alone. The others are flagged for `bdnex review` (nothing is "
               "unlinked), or, with --unlink, unlinked so that `bdnex import --skipped --auto=all` decides them again "
               "(strict, and the covers of the best candidates vote). No request is sent. Files chosen by you or read from "
               "their own tags are never touched.")
    parser.add_argument("query", nargs="*", help="only these files, same terms as `bdnex ls --files`")
    parser.add_argument("--unlink", action="store_true",
                        help="unlink the ones something contradicts, instead of flagging them (the ones nothing confirms are "
                             "still only flagged, unless --unconfirmed)")
    parser.add_argument("--unconfirmed", action="store_true",
                        help="with --unlink: unlink the ones nothing confirms too (the most careful: a right match whose cover "
                             "could not be compared comes back only if its cover votes)")
    parser.add_argument("--no-cover", action="store_true",
                        help="do not compare the covers that were not compared yet (the file is read for each)")
    parser.add_argument("--json", action="store_true", help="the verdicts as JSON")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation before unlinking")
    add_pretend(parser, "be flagged or unlinked")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    settings = context.settings()
    if settings is None:
        return 1
    if not context.has_library(settings, "nothing to recheck."):
        return 1
    on = color.enabled(out)
    with Library.for_settings(settings, readonly=args.pretend) as library:
        try:
            verdicts = weak_matches(library, settings, args.query, compare=not args.no_cover, store=not args.pretend)
        except QueryError as err:
            return context.bad_query(err)
        count = Counter(v.kind for v in verdicts)
        if args.json:
            print(json.dumps([v.as_dict() for v in verdicts], ensure_ascii=False, indent=2), file=out)
            return 0
        print(f"{len(verdicts)} match(es) applied under {settings.match.strong_threshold:.0f}: "
              + ", ".join(f"{count[kind]} {kind}" for kind in (KEPT, UNCONFIRMED, WRONG)), file=out)
        for kind in (WRONG, UNCONFIRMED):
            shown = [v for v in verdicts if v.kind == kind]
            if shown and (args.verbose or kind == WRONG):
                print(f"\n{color.paint(kind, 'red' if kind == WRONG else 'yellow', on)}: {MEANING[kind]}", file=out)
                for v in shown[: None if args.verbose else 20]:
                    print(f"  {v.score:>3.0f}%  {v.album}\n        {v.path}", file=out)
                    for text in v.for_:
                        print(f"        + {text}", file=out)
                    for text in v.against:
                        print(f"        - {text}", file=out)
                if len(shown) > 20 and not args.verbose:
                    print(f"  ... and {len(shown) - 20} more (-v lists them all)", file=out)
        flag = [v for v in verdicts if v.kind in (WRONG, UNCONFIRMED)]
        unlink = [v for v in flag if v.kind == WRONG or args.unconfirmed] if args.unlink else []
        if not flag or args.pretend:
            if flag:
                print(f"\n{len(unlink)} would be unlinked, {len(flag) - len(unlink)} flagged for `bdnex review`.", file=out)
            return 0
        if unlink:
            if not args.yes:
                if prompter is None:
                    from bdnex.ui.prompts import new_prompter
                    prompter = new_prompter()
                if not prompter.confirm(f"Unlink {len(unlink)} file(s) from their album?"):
                    print("Nothing changed.", file=out)
                    return 1
            for v in unlink:
                row = library.get_file(v.path)
                settle(library, {"path": v.path, "cover_score": row.get("cover_score") if row else None}, "unlink")
        for v in flag:
            if v not in unlink:
                library.flag_review(v.path, "applied at {:.0f}%: {}".format(v.score, "; ".join(v.against) or "nothing confirms it"))
        if unlink:
            print(f"\nUnlinked {len(unlink)} file(s): `bdnex import --skipped --auto=all` decides them again "
                  "(strict, then the covers vote).", file=out)
        if len(flag) > len(unlink):
            print(("" if unlink else "\n") + f"Flagged {len(flag) - len(unlink)} file(s) for `bdnex review`.", file=out)
    return 0
