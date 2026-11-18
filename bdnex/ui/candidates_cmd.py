"""``bdnex candidates``: for files whose match is in doubt, the other albums they could be, with the evidence."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from bdnex.importer import confirm
from bdnex.importer.matching import find_matches
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui import context
from bdnex.ui.describe import label
from bdnex.ui.output import add_verbosity, setup_logging

OFFLINE = "bdnex.importer.registry:offline_fetch"


def as_album(info) -> dict[str, Any]:
    """An :class:`AlbumInfo` (or a library album) as the flat dict :func:`confirm.assess` reads."""
    if isinstance(info, dict):
        return info
    return {"title": info.title, "series": info.series.name if info.series else None, "volume": info.volume,
            "year": info.year, "publisher": info.publisher, "credits": info.credits, "extra": info.extra}


def is_low(library, settings, path: str, file: dict | None, album: dict | None, last: dict | None) -> str:
    """Why this file's match is in doubt (``""``: it is not): no album, flagged, looks wrong, or a weak score."""
    if album is None:
        return "no album"
    if file and file.get("review"):
        return "flagged for review"
    evidence = confirm.assess(path, album, (file or {}).get("embedded") or None)
    if confirm.worth_a_look(evidence):
        return f"looks {evidence.verdict}"
    strong = settings.match.strong_threshold
    if last and last.get("score") is not None and last["score"] < strong and last["outcome"] != "tagged":
        return f"matched with a score of {last['score']:.0f}"
    return ""


def describe_candidate(library, source, match, file, current_id) -> dict[str, Any]:
    known = library.find_album(source.name, match.candidate.source_id)
    album = library.get_album(known) if known is not None else None
    complete = bool(album) and not album["extra"].get("partial")
    shown = album if complete else as_album(source.partial_info(match.candidate))
    evidence = confirm.assess(file["path"], shown, file.get("embedded") or None)
    return {"score": round(match.score), "album": label(shown), "url": match.candidate.url,
            "year": shown.get("year"), "publisher": shown.get("publisher"), "complete": complete,
            "current": known is not None and known == current_id,
            "verdict": evidence.verdict, "for": evidence.agree, "against": evidence.strong + evidence.weak}


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex candidates", description=__doc__,
        epilog="Nothing is changed and nothing is requested: the candidates come from the album list stored in the "
               "library. To redo a file: `bdnex import --match --force FILE`.")
    parser.add_argument("query", nargs="*", help="files to look at, same terms as `bdnex ls --files`")
    parser.add_argument("--low", action="store_true",
                        help="only the files whose match is in doubt: no album, flagged, looking wrong (see `bdnex review "
                             "--scan`), or matched with a score under `match.strong_threshold`")
    parser.add_argument("--limit", type=int, default=5, metavar="N", help="candidates per file (default 5)")
    parser.add_argument("--json", action="store_true", help="JSON, for a script or an assistant")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    settings = context.settings()
    if settings is None:
        return 1
    offline = settings.model_copy(update={"bedetheque": settings.bedetheque.model_copy(
        update={"fetcher": OFFLINE, "delay": (0, 0), "sitemap_delay": (0, 0)})})

    report = []
    with Library.for_settings(settings, readonly=True) as library:
        try:
            source = create_source(settings.source, library, offline)
            linked = library.query(args.query, files=True)
            unlinked = library.unmatched(args.query)
        except (UnknownSource, TypeError) as err:
            print(err, file=sys.stderr)
            return 1
        except QueryError as err:
            return context.bad_query(err)
        if not source.is_ready():
            print("The album list is not downloaded yet: run `bdnex init` first.", file=sys.stderr)
            return 1
        warm = getattr(source, "warm", None)
        if warm:
            warm()
        work = {row["path"]: row["id"] for row in linked}
        for row in unlinked:
            work.setdefault(row["path"], None)  # a file without album can be in both lists
        for path, album_id in sorted(work.items()):
            file = library.get_file(path)
            album = library.get_album(album_id) if album_id is not None else None
            why = is_low(library, settings, path, file, album, library.last_import(path)) if args.low else "asked for"
            if not why:
                continue
            matches = find_matches(source, path, limit=args.limit)
            report.append({"path": path, "why": why, "now": label(album) if album else None,
                           "candidates": [describe_candidate(library, source, m, file or {"path": path}, album_id)
                                          for m in matches]})
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=out)
        return 0
    for entry in report:
        print(f"\n{entry['path']}\n  now: {entry['now'] or '(no album)'}   [{entry['why']}]", file=out)
        for number, c in enumerate(entry["candidates"], start=1):
            mark = "*" if c["current"] else " "
            print(f" {mark}{number}. {c['score']:>3}  {c['album']}  {c['year'] or ''} {c['publisher'] or ''}".rstrip()
                  + f"  [{c['verdict']}{'' if c['complete'] else ', page not read'}]", file=out)
            for text in c["for"]:
                print(f"        + {text}", file=out)
            for text in c["against"]:
                print(f"        - {text}", file=out)
    print(f"\n{len(report)} file(s). `*` is the album now linked. Redo one with `bdnex import --match --force FILE`.", file=out)
    return 0
