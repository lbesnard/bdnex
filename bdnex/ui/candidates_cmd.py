"""``bdnex candidates``: for files whose match is in doubt, the other albums they could be, with the evidence."""
# (the finding is `importer/candidates.py`; this module is the command and how it prints; the docstring is the command's help)

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Optional

from bdnex.importer import covers
from bdnex.importer.candidates import NotReady, build_report, iter_report
from bdnex.importer.registry import UnknownSource
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui import context
from bdnex.ui.output import add_verbosity, setup_logging

logger = logging.getLogger(__name__)

VERDICT_KIND = {"confirmed": "confirmed", "unverified": "unverified", "doubtful": "doubtful", "contradicted": "contradicted"}


def head_lines(number: int, total: int, path: str, now: Optional[str], why: str, on: bool) -> list[str]:
    """The lines that say which file is being looked at, and what it is linked to now."""
    from bdnex.ui import color

    return ["", f"{color.role(f'[{number}/{total}]', 'hint', on)} {color.role(os.path.basename(path), 'name', on)}",
            f"      {color.role(path, 'path', on)}",
            f"      now: {color.role(now, 'name', on) if now else color.role('(no album)', 'warn', on)}   "
            f"{color.role(f'[{why}]', 'note', on)}"]


def decision_lines(entry: dict[str, Any], on: bool) -> list[str]:
    """What the covers say about a file: which album's cover is the file's, whether it is the one linked now, and what to type."""
    import shlex

    from bdnex.ui import color

    best = next((c for c in entry["candidates"] if c.get("cover_best")), None)
    if best is None:
        return ["      " + color.role("=> no candidate's cover looks like the file's", "warn", on)]
    now = next((c for c in entry["candidates"] if c["current"]), None)
    if best["current"]:
        return ["      " + color.role(f"=> the album now linked is the one whose cover is the file's ({best['cover']}%)", "good", on)]
    lines = ["      " + color.role(f"=> the cover of {best['album']} is the file's ({best['cover']}%)", "good", on)
             + (color.role(f", not the album now linked ({now['album']}, {now['cover']}%)", "warn", on)
                if now and now.get("cover") is not None else "")]
    lines.append("      " + color.hints(f"to use it: `bdnex import --album {best['url']} {shlex.quote(entry['path'])}`", on))
    return lines


def candidate_lines(number: int, c: dict[str, Any], on: bool) -> list[str]:
    """One candidate: its number, score, album and what the evidence says; the evidence under it."""
    from bdnex.ui import color

    mark = color.role("*", "hint", on) if c["current"] else " "
    score = color.paint_score(f"{c['score']:>3}", float(c["score"]), on)
    verdict = color.verdict(c["verdict"] + ("" if c["complete"] else ", page not read"), VERDICT_KIND.get(c["verdict"], "unverified"), on)
    cover = ""
    if c.get("cover") is not None:
        cover = "  cover " + color.paint_score(f"{c['cover']}%", float(c["cover"]), on)
        if c.get("cover_best"):
            cover += color.role(" <- most like the file's", "good", on)
    elif "cover" in c:
        cover = "  " + color.role(c.get("cover_note") or "cover not compared", "warn", on)
    extra = f"{c['year'] or ''} {c['publisher'] or ''}".strip()
    lines = [f" {mark}{number}. {score}  {color.role(c['album'], 'name', on)}  {color.role(extra, 'note', on)}"
             f"  [{verdict}]{cover}"]
    lines += [f"        {color.role('= ' + text, 'note', on)}" for text in c["why_score"]]
    lines += [f"        {color.role('+ ' + text, 'good', on)}" for text in c["for"]]
    lines += [f"        {color.role('- ' + text, 'bad', on)}" for text in c["against"]]
    return lines


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex candidates", description=__doc__,
        epilog="Nothing is changed. The candidates come from the album list stored in the library, so without --cover "
               "nothing is requested. To redo a file: `bdnex import --match --force FILE`, or `bdnex review --auto` to "
               "settle the doubtful ones by rule.")
    parser.add_argument("query", nargs="*", help="files to look at, same terms as `bdnex ls --files`")
    parser.add_argument("--low", action="store_true",
                        help="only the files whose match is in doubt: no album, flagged, looking wrong (see `bdnex check "
                             "--matches`), or matched with a score under `match.strong_threshold`")
    parser.add_argument("--all", action="store_true",
                        help="look at every file of the library: needed when you give neither a query nor --low (with --cover "
                             "that can be tens of thousands of cover requests)")
    parser.add_argument("--limit", type=int, default=5, metavar="N", help="candidates per file (default 5)")
    parser.add_argument("--cover", action="store_true",
                        help="also compare the file's cover with the cover of each candidate (a cover the import already saved "
                             "is read from disk; any other is one request, so keep `--limit` small; needs OpenCV). The candidate whose cover is most like the file's "
                             "is marked")
    parser.add_argument("--json", action="store_true", help="JSON, for a script or an assistant")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    if not (args.query or args.low or args.all):  # every file of the library, by accident: say so instead
        print("That is every file of the library. Give a query (`bdnex candidates series:nains`), `--low` (only the files "
              "whose match is in doubt), or `--all` if you really mean every file.", file=sys.stderr)
        return 2
    settings = context.settings()
    if settings is None:
        return 1
    if args.cover and not covers.opencv_installed():
        print("--cover needs OpenCV (`pip install opencv-python-headless`): the covers are not compared.", file=sys.stderr)
        args.cover = False
    from bdnex.ui import color

    on = color.enabled(out)
    report = []
    with Library.for_settings(settings, readonly=True) as library:
        try:
            if args.json:  # the whole report at the end, nothing else on stdout
                report = build_report(settings, library, args.query, low=args.low, limit=args.limit, cover=args.cover)
            else:  # each file as it is ready: the file first (the covers take a while), then what was found for it
                def head(number, total, path, now, why):
                    print("\n".join(head_lines(number, total, path, now, why, on)), file=out, flush=True)

                def announce(total):
                    if total is None:
                        print(color.role("Finding the files to look at...", "note", on), file=sys.stderr, flush=True)
                    else:
                        more = (f" (at most {total * args.limit} cover requests, fewer for the covers already saved; Ctrl-C stops)"
                                if args.cover else "")
                        print(f"{color.role(str(total), 'count', on)} file(s) to look at{more}.", file=out, flush=True)

                def step(event, number, entry, url):
                    """Each cover as it is downloaded and compared: the album it belongs to, then what was found."""
                    if event in ("download", "saved"):
                        what = "downloading" if event == "download" else "using the saved cover of"
                        text = f"{number}. {what}{' the cover of' if event == 'download' else ''} {entry['album']}"
                        print(f"      {color.role(text, 'note', on)}" + (f"  {color.role(url, 'path', on)}" if event == "download" else ""),
                              file=out, flush=True)
                    else:
                        print("\n".join(candidate_lines(number, entry, on)), file=out, flush=True)

                for entry in iter_report(settings, library, args.query, low=args.low, limit=args.limit, cover=args.cover,
                                         progress=head, step=step, announce=announce):
                    report.append(entry)
                    if not args.cover:  # nothing slow happened: all the candidates at once
                        for number, c in enumerate(entry["candidates"], start=1):
                            print("\n".join(candidate_lines(number, c, on)), file=out, flush=True)
                    elif entry["candidates"]:
                        print("\n".join(decision_lines(entry, on)), file=out, flush=True)
                    if not entry["candidates"]:
                        print(f"      {color.role('no candidate in the album list', 'warn', on)}", file=out)
        except (UnknownSource, TypeError, NotReady) as err:
            print(err, file=sys.stderr)
            return 1
        except QueryError as err:
            return context.bad_query(err)
        except KeyboardInterrupt:
            print(f"\n{color.role('Interrupted', 'warn', on)}: {len(report)} file(s) shown.", file=sys.stderr)
            return 130
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=out)
        return 0
    print(color.hints(f"\n{len(report)} file(s). `*` is the album now linked. Redo one with `bdnex import --match --force FILE`, "
                      "or settle them all with `bdnex review --auto`.", on), file=out)
    return 0
