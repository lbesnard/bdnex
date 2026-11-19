"""``bdnex recheck``: the matches applied below the strong threshold, weighed again with what the library knows."""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from collections import Counter

from bdnex.importer import covers
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.library.recheck import KEPT, UNCONFIRMED, WRONG, Verdict, weak_matches
from bdnex.ui import color, context
from bdnex.ui.output import add_pretend, add_verbosity, setup_logging
from bdnex.ui.review_cmd import settle

logger = logging.getLogger(__name__)

MEANING = {KEPT: "confirmed by something other than the name", WRONG: "something contradicts it",
           UNCONFIRMED: "nothing confirms it"}
OWN_FLAG = "applied at"  # the start of the reason ``recheck`` gives; a flag of its own can be taken back


def add_candidate_options(parser) -> None:
    parser.add_argument("--candidates", type=int, default=5, metavar="N",
                        help="for each contradicted file, compare the covers of its N best other candidates (the saved "
                             "cover when there is one, else one request each; needs OpenCV) and propose the album whose "
                             "cover is clearly the file's; 0: do not (default 5)")
    parser.add_argument("--no-cover", action="store_true",
                        help="do not compare covers at all (neither the album's, not yet compared, nor the candidates')")


def look_for_better(settings, library, verdicts: list[Verdict], limit: int, say=lambda text: None) -> int:
    """Set ``better`` on the contradicted verdicts: another candidate whose cover is clearly the file's (the rule of the
    cover vote of an import). Returns how many were found. Errors are swallowed: it is a help, never a reason to fail."""
    wrong = [v for v in verdicts if v.kind == WRONG]
    if limit <= 0 or not wrong:
        return 0
    if not covers.opencv_installed():
        say("The covers of the other candidates are not compared: OpenCV is not installed (`uv sync --extra cover`).")
        return 0
    from bdnex.importer.matching import find_matches
    from bdnex.importer.registry import create_source
    from bdnex.importer.session_vote import LEAD
    from bdnex.ui.candidates_cmd import compare_covers

    try:
        source = create_source(settings.source, library, settings)
        if not source.is_ready():
            say("The covers of the other candidates are not compared: the album list is not downloaded (`bdnex init`).")
            return 0
        warm = getattr(source, "warm", None)
        if warm:
            warm()
    except Exception as err:
        say(f"The covers of the other candidates are not compared: {err}")
        return 0
    say(f"Comparing the covers of the {limit} best other candidates of {len(wrong)} contradicted file(s) "
        "(a cover not saved yet is one request; Ctrl-C stops)...")
    unreadable = 0
    medium, vote_min = settings.match.medium_threshold, settings.match.cover_vote_min
    found = 0
    for number, verdict in enumerate(wrong, start=1):
        if number % 10 == 0:
            say(f"  covers of the other candidates: {number}/{len(wrong)}, {found} better album(s) found")
        try:
            row = library.get_file(verdict.path) or {}
            matches = find_matches(source, verdict.path, limit=limit)
            entries = [{"for": [], "against": []} for _ in matches]
            compare_covers(source, settings, verdict.path, row.get("format"), matches, entries)
            if matches and all(entry.get("cover") is None for entry in entries):
                unreadable += 1  # the file's first page could not be read (a disk not mounted?) or no cover could be fetched
        except KeyboardInterrupt:
            raise
        except Exception as err:
            logger.debug(f"{verdict.path}: candidates not compared ({err})")
            continue
        scored = []
        for match, entry in zip(matches, entries):
            if entry.get("cover") is None:
                continue
            known = library.find_album(source.name, match.candidate.source_id)
            scored.append((entry["cover"], match, known))
        mine = [cover for cover, _, known in scored if known == verdict.album_id]
        others = [(cover, match, known) for cover, match, known in scored if known != verdict.album_id and match.score >= medium]
        if not others:
            continue
        top = max(others, key=lambda item: item[0])
        rest = [cover for cover, match, _ in scored if match is not top[1]]
        beaten = max([*rest, *mine, verdict.cover or 0.0])
        if top[0] >= vote_min and top[0] - beaten >= LEAD:
            known = library.get_album(top[2]) if top[2] is not None else None
            name = f"{known.get('series') or ''} {known['title']}".strip() if known else top[1].candidate.name
            verdict.better = {"url": top[1].candidate.url, "album": name, "score": round(top[1].score),
                              "cover": top[0], "current_cover": beaten}
            found += 1
    say(f"Covers compared: {found} better album(s) found"
        + (f"; for {unreadable} file(s) no cover could be compared (is the disk mounted? `-vv` says why)" if unreadable else "")
        + ".")
    return found


def rematch(path: str, url: str) -> bool:
    """Link the file to the album at ``url`` as ``bdnex import --album`` does (the library only; no file is touched)."""
    from bdnex.ui import import_cmd

    code = import_cmd.run([path, "--album", url, "--database-only"], out=io.StringIO())
    return code == 0


def better_text(v: Verdict) -> str:
    b = v.better
    return f"{b['album']} (its cover is {b['cover']:.0f}% like the file's, the others' at most {b['current_cover']:.0f}%)"


def settle_verdicts(library, verdicts: list[Verdict], *, unlink: bool, unconfirmed: bool = False, use_better: bool = False,
                    flag_unconfirmed: bool = False, own_flags_only: bool = True, yes: bool = False, prompter=None,
                    out=sys.stdout) -> dict[str, int]:
    """Apply the verdicts: the one place for ``recheck`` and ``review --auto``.

    kept: the flag goes (a flag of ``recheck``'s own, or any with ``own_flags_only=False``). wrong: switched to the
    better album (``use_better``), else unlinked (``unlink``), else flagged. unconfirmed: left as it is (flagged only with
    ``flag_unconfirmed``; unlinked with ``unlink`` and ``unconfirmed``)."""
    count = {"cleared": 0, "switched": 0, "unlinked": 0, "flagged": 0}
    reviews = {v.path: (library.get_file(v.path) or {}).get("review") or "" for v in verdicts}

    def ours(v: Verdict) -> bool:
        return bool(reviews[v.path]) and (not own_flags_only or reviews[v.path].startswith(OWN_FLAG))

    switch = [v for v in verdicts if v.kind == WRONG and v.better and use_better]
    cut = [v for v in verdicts if v not in switch and unlink and (v.kind == WRONG or (v.kind == UNCONFIRMED and unconfirmed))]
    if (switch or cut) and not yes:
        if prompter is None:
            from bdnex.ui.prompts import new_prompter
            prompter = new_prompter()
        what = ", ".join(part for part in (f"switch {len(switch)} file(s) to a better album" if switch else "",
                                           f"unlink {len(cut)} file(s) from their album" if cut else "") if part)
        if not prompter.confirm(f"{what.capitalize()}?"):
            print("Nothing changed.", file=out)
            raise _Declined
    for v in verdicts:
        takes_back = v.kind == KEPT or (v.kind == UNCONFIRMED and own_flags_only and not (v in cut or flag_unconfirmed))
        if takes_back and ours(v):
            library.clear_review(v.path)
            count["cleared"] += 1
    for v in switch:
        if rematch(v.path, v.better["url"]):
            count["switched"] += 1
        else:
            print(f"  could not switch {v.path} to {v.better['url']}: it stays as it was", file=out)
    for v in cut:
        row = library.get_file(v.path)
        settle(library, {"path": v.path, "cover_score": row.get("cover_score") if row else None}, "unlink")
        count["unlinked"] += 1
    for v in verdicts:
        if v in cut or v in switch or v.kind == KEPT:
            continue
        if v.kind == UNCONFIRMED and not flag_unconfirmed:
            continue
        reason = "; ".join(v.against) or "nothing confirms it"
        if v.better:
            reason += f"; better: {better_text(v)}"
        library.flag_review(v.path, ("{} {:.0f}%: {}".format(OWN_FLAG, v.score, reason) if v.score is not None
                                     else f"{OWN_FLAG} an earlier import: {reason}"))
        count["flagged"] += 1
    return count


class _Declined(Exception):
    pass


def run(argv: list[str], out=sys.stdout, prompter=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex recheck", description=__doc__,
        epilog="For each file linked with a score under `match.strong_threshold` (a medium match, applied before "
               "`import --auto=all` became strict): its album's page, the file's own tags and its cover against the album's "
               "saved cover. Confirmed ones are left alone, and so are the ones nothing confirms (they are not flagged: "
               "nothing says they are wrong). The contradicted ones are flagged for `bdnex review` (nothing is unlinked), "
               "or unlinked with --unlink so that `bdnex import --skipped --auto=all` decides them again (strict, and the "
               "covers of the best candidates vote). For each contradicted file the covers of its best other candidates are "
               "compared too (--candidates): when one clearly looks like the file's, it is proposed, and with --rematch "
               "the file is linked to it. Only those covers are requested; no page is. Files chosen by you or read from "
               "their own tags are never touched.")
    parser.add_argument("query", nargs="*", help="only these files, same terms as `bdnex ls --files`")
    parser.add_argument("--unlink", action="store_true",
                        help="unlink the ones something contradicts (and for which no better album was found), instead of "
                             "flagging them")
    parser.add_argument("--unconfirmed", action="store_true",
                        help="with --unlink: unlink the ones nothing confirms too (the most careful: a right match whose cover "
                             "could not be compared comes back only if its cover votes)")
    parser.add_argument("--rematch", action="store_true",
                        help="link a contradicted file to the better album that --candidates found (the library only)")
    parser.add_argument("--flag-unconfirmed", action="store_true",
                        help="flag the ones nothing confirms for `bdnex review` too (by default they are left alone)")
    add_candidate_options(parser)
    parser.add_argument("--json", action="store_true", help="the verdicts as JSON")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation before unlinking or switching")
    add_pretend(parser, "be flagged, unlinked or switched")
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
        if not args.no_cover:
            try:
                look_for_better(settings, library, verdicts, args.candidates,
                                say=lambda text: print(text, file=sys.stderr, flush=True))
            except KeyboardInterrupt:
                print("Interrupted: the covers of the other candidates are not all compared.", file=sys.stderr)
        count = Counter(v.kind for v in verdicts)
        better = [v for v in verdicts if v.better]
        if args.json:
            print(json.dumps([v.as_dict() for v in verdicts], ensure_ascii=False, indent=2), file=out)
            return 0
        print(f"{len(verdicts)} match(es) applied under {settings.match.strong_threshold:.0f}: "
              + ", ".join(f"{count[kind]} {kind}" for kind in (KEPT, UNCONFIRMED, WRONG))
              + (f"; for {len(better)} of the wrong ones another album's cover is clearly the file's" if better else ""),
              file=out)
        for kind in (WRONG, UNCONFIRMED):
            shown = [v for v in verdicts if v.kind == kind]
            if shown and (args.verbose or kind == WRONG):
                print(f"\n{color.paint(kind, 'red' if kind == WRONG else 'yellow', on)}: {MEANING[kind]}", file=out)
                for v in shown[: None if args.verbose else 20]:
                    print(f"  {'  - ' if v.score is None else format(v.score, '>3.0f') + '%'}  {v.album}\n        {v.path}", file=out)
                    for text in v.for_:
                        print(f"        + {text}", file=out)
                    for text in v.against:
                        print(f"        - {text}", file=out)
                    if v.better:
                        print(f"        > better: {better_text(v)}\n          {v.better['url']}", file=out)
                if len(shown) > 20 and not args.verbose:
                    print(f"  ... and {len(shown) - 20} more (-v lists them all)", file=out)
        if args.pretend:
            switch = len(better) if args.rematch else 0
            unlink = sum(1 for v in verdicts if args.unlink and not (args.rematch and v.better)
                         and (v.kind == WRONG or (v.kind == UNCONFIRMED and args.unconfirmed)))
            flag = sum(1 for v in verdicts if v.kind == WRONG or (args.flag_unconfirmed and v.kind == UNCONFIRMED)) - unlink - switch
            if switch or unlink or flag:
                print(f"\n{switch} would be switched to a better album, {unlink} unlinked, {flag} flagged for `bdnex review`.",
                      file=out)
            return 0
        try:
            done = settle_verdicts(library, verdicts, unlink=args.unlink, unconfirmed=args.unconfirmed,
                                   use_better=args.rematch, flag_unconfirmed=args.flag_unconfirmed, yes=args.yes,
                                   prompter=prompter, out=out)
        except _Declined:
            return 1
    print(file=out)
    if done["switched"]:
        print(f"Switched {done['switched']} file(s) to the album whose cover is the file's.", file=out)
    if done["unlinked"]:
        print(f"Unlinked {done['unlinked']} file(s): `bdnex import --skipped --auto=all` decides them again "
              "(strict, then the covers vote).", file=out)
    if done["flagged"]:
        print(f"Flagged {done['flagged']} file(s) for `bdnex review` (`bdnex review --auto` settles them by rule).", file=out)
    if done["cleared"]:
        print(f"Took back {done['cleared']} flag(s) of an earlier recheck that are not contradicted.", file=out)
    if not any(done.values()):
        print("Nothing to change.", file=out)
    return 0
