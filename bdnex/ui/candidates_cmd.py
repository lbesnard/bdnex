"""``bdnex candidates``: for files whose match is in doubt, the other albums they could be, with the evidence."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from bdnex.importer import confirm, covers
from bdnex.importer.matching import find_matches
from bdnex.importer.registry import UnknownSource, create_source
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui import context
from bdnex.ui.describe import label
from bdnex.ui.output import add_verbosity, setup_logging

logger = logging.getLogger(__name__)
OFFLINE = "bdnex.importer.registry:offline_fetch"


def is_low(settings, path: str, file: dict | None, album: dict | None, last: dict | None) -> str:
    """Why this file's match is in doubt (``""``: it is not): no album, flagged, looks wrong, or a weak score."""
    if album is None:
        return "no album"
    if file and file.get("review"):
        return "flagged for review"
    evidence = confirm.judge(path, album, (file or {}).get("embedded") or None)
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
    shown = album if complete else confirm.album_from_info(source.partial_info(match.candidate))
    evidence = confirm.assess(file["path"], shown, file.get("embedded") or None)
    return {"score": round(match.score), "album": label(shown), "url": match.candidate.url,
            "year": shown.get("year"), "publisher": shown.get("publisher"), "complete": complete,
            "current": known is not None and known == current_id,
            "verdict": evidence.verdict, "for": evidence.agree, "against": evidence.strong + evidence.weak}


def compare_covers(source, settings, path: str, fmt, matches, found: list[dict[str, Any]]) -> None:
    """Add ``cover`` (0-100, or ``None`` when it could not be compared) to each candidate, and mark the best one."""
    local = covers.front_cover(path, fmt or "cbz")
    threshold = settings.cover.match_percentage
    for match, entry in zip(matches, found):
        entry["cover"] = None
        if not local:
            continue
        known = source.library.find_album(source.name, match.candidate.source_id)
        album = source.library.get_album(known) if known else None
        url = (album or {}).get("cover_url") or source.guess_cover_url(match.candidate)
        try:
            online = source.cover_image(url)
            covers.check_cover_image(online)
            entry["cover"] = round(covers.sift_similarity(local, online))
        except (covers.CoverError, *source.fetch_errors) as err:
            logger.debug(f"{path}: cover of {url} not compared ({err})")
            continue
        (entry["for"] if entry["cover"] >= threshold else entry["against"]).append(f"its cover is {entry['cover']}% like the file's")
    scored = [e for e in found if e.get("cover") is not None]
    best = max(scored, key=lambda e: e["cover"], default=None)
    for entry in found:
        entry["cover_best"] = entry is best and best["cover"] >= threshold


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex candidates", description=__doc__,
        epilog="Nothing is changed and nothing is requested: the candidates come from the album list stored in the "
               "library. To redo a file: `bdnex import --match --force FILE`.")
    parser.add_argument("query", nargs="*", help="files to look at, same terms as `bdnex ls --files`")
    parser.add_argument("--low", action="store_true",
                        help="only the files whose match is in doubt: no album, flagged, looking wrong (see `bdnex check "
                             "--matches`), or matched with a score under `match.strong_threshold`")
    parser.add_argument("--limit", type=int, default=5, metavar="N", help="candidates per file (default 5)")
    parser.add_argument("--cover", action="store_true",
                        help="also compare the file's cover with the cover of each candidate (one request per candidate, "
                             "so keep `--limit` small; needs OpenCV). The candidate whose cover is most like the file's "
                             "is marked")
    parser.add_argument("--json", action="store_true", help="JSON, for a script or an assistant")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    settings = context.settings()
    if settings is None:
        return 1
    offline = settings if args.cover else settings.model_copy(update={"bedetheque": settings.bedetheque.model_copy(
        update={"fetcher": OFFLINE, "delay": (0, 0), "sitemap_delay": (0, 0)})})

    report = []
    if args.cover and not covers.opencv_installed():
        print("--cover needs OpenCV (`pip install 'bdnex[cover]'`): the covers are not compared.", file=sys.stderr)
        args.cover = False
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
            why = is_low(settings, path, file, album, library.last_import(path)) if args.low else "asked for"
            if not why:
                continue
            matches = find_matches(source, path, limit=args.limit)
            found = [describe_candidate(library, source, m, file or {"path": path}, album_id) for m in matches]
            if args.cover:
                compare_covers(source, settings, path, (file or {}).get("format"), matches, found)
            report.append({"path": path, "why": why, "now": label(album) if album else None, "candidates": found})
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=out)
        return 0
    for entry in report:
        print(f"\n{entry['path']}\n  now: {entry['now'] or '(no album)'}   [{entry['why']}]", file=out)
        for number, c in enumerate(entry["candidates"], start=1):
            mark = "*" if c["current"] else " "
            cover = "" if c.get("cover") is None else f"  cover {c['cover']}%" + (" <- most like the file's" if c.get("cover_best") else "")
            print(f" {mark}{number}. {c['score']:>3}  {c['album']}  {c['year'] or ''} {c['publisher'] or ''}".rstrip()
                  + f"  [{c['verdict']}{'' if c['complete'] else ', page not read'}]{cover}", file=out)
            for text in c["for"]:
                print(f"        + {text}", file=out)
            for text in c["against"]:
                print(f"        - {text}", file=out)
    print(f"\n{len(report)} file(s). `*` is the album now linked. Redo one with `bdnex import --match --force FILE`.", file=out)
    return 0
