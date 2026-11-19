"""``bdnex candidates``: for files whose match is in doubt, the other albums they could be, with the evidence."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Optional

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


def why_score(source, path: str, match) -> list[str]:
    """Where a candidate's score comes from, in words: the title score (of the query that gave the most), then what moved it."""
    from bdnex.importer.matching import queries_for, series_queries

    explain = getattr(source, "explain", None)
    best: tuple[float, list[str]] = (-1.0, [])
    if explain is not None:
        for text in [*queries_for(path), *series_queries(path)]:
            try:
                found = explain(text, match.candidate)
            except Exception as err:  # an explanation is a help: never a reason to fail
                logger.debug(f"{path}: no explanation for {text!r} ({err})")
                continue
            if found[0] > best[0]:
                best = found
    lines = [f"title: {best[0]:.0f}" + (f" ({'; '.join(best[1])})" if best[1] else "")] if best[0] >= 0 else []
    lines += list(match.parts)
    return lines


def describe_candidate(library, source, match, file, current_id) -> dict[str, Any]:
    known = library.find_album(source.name, match.candidate.source_id)
    album = library.get_album(known) if known is not None else None
    complete = bool(album) and not album["extra"].get("partial")
    shown = album if complete else confirm.album_from_info(source.partial_info(match.candidate))
    evidence = confirm.assess(file["path"], shown, file.get("embedded") or None)
    return {"score": round(match.score), "album": label(shown), "url": match.candidate.url,
            "year": shown.get("year"), "publisher": shown.get("publisher"), "complete": complete,
            "current": known is not None and known == current_id, "why_score": why_score(source, file["path"], match),
            "verdict": evidence.verdict, "for": evidence.agree, "against": evidence.strong + evidence.weak}


def saved_cover(source, settings, source_id: str, album) -> Optional[bytes]:
    """The cover file the import kept for this album (``cover.download``), or ``None``: then it has to be requested."""
    kept = (album or {}).get("cover_file") or covers.find_cover(settings.cover_path, source.name, source_id)
    path = settings.cover_path / kept if kept else None
    try:
        return path.read_bytes() if path and path.exists() else None
    except OSError:
        return None


def compare_covers(source, settings, path: str, fmt, matches, found: list[dict[str, Any]]) -> None:
    """Add ``cover`` (0-100, or ``None`` when it could not be compared) to each candidate, and mark the best one."""
    local = covers.front_cover(path, fmt or "cbz")
    threshold = settings.cover.match_percentage
    for match, entry in zip(matches, found):
        entry["cover"] = None
        if not local:
            entry["cover_note"] = "the file's first page could not be read"
            continue
        known = source.library.find_album(source.name, match.candidate.source_id)
        album = source.library.get_album(known) if known else None
        url = (album or {}).get("cover_url") or source.guess_cover_url(match.candidate)
        try:
            online = saved_cover(source, settings, match.candidate.source_id, album)  # the import already downloaded it
            entry["cover_from"] = "saved" if online is not None else "downloaded"
            if online is None:
                online = source.cover_image(url)
            covers.check_cover_image(online)
            entry["cover"] = round(covers.sift_similarity(local, online))
        except (covers.CoverError, *source.fetch_errors) as err:
            logger.debug(f"{path}: cover of {url} not compared ({err})")
            entry["cover_note"] = f"not compared: {err}"
            continue
        (entry["for"] if entry["cover"] >= threshold else entry["against"]).append(f"its cover is {entry['cover']}% like the file's")
    scored = [e for e in found if e.get("cover") is not None]
    best = max(scored, key=lambda e: e["cover"], default=None)
    for entry in found:
        entry["cover_best"] = entry is best and best["cover"] >= threshold


class NotReady(RuntimeError):
    """The album list is not downloaded yet."""


def build_report(settings, library, query: list[str], low: bool = False, limit: int = 5, cover: bool = False,
                 first: int = 0, files: Optional[int] = None) -> list[dict[str, Any]]:
    """The candidates of each file the query names (the report of ``bdnex candidates --json``, also the web page's).

    ``first`` / ``files`` take a window of the (sorted) files: with ``--cover`` every file costs requests, so the
    web page asks for a few at a time. Raises ``QueryError``, ``NotReady`` or the source's own errors."""
    offline = settings if cover else settings.model_copy(update={"bedetheque": settings.bedetheque.model_copy(
        update={"fetcher": OFFLINE, "delay": (0, 0), "sitemap_delay": (0, 0)})})
    source = create_source(settings.source, library, offline)
    linked = library.query(query, files=True)
    unlinked = library.unmatched(query)
    if not source.is_ready():
        raise NotReady("The album list is not downloaded yet: run `bdnex init` first.")
    warm = getattr(source, "warm", None)
    if warm:
        warm()
    work = {row["path"]: row["id"] for row in linked}
    for row in unlinked:
        work.setdefault(row["path"], None)  # a file without album can be in both lists
    report = []
    for path, album_id in sorted(work.items()):
        file = library.get_file(path)
        album = library.get_album(album_id) if album_id is not None else None
        why = is_low(settings, path, file, album, library.last_import(path)) if low else "asked for"
        if not why:
            continue
        if len(report) < first:  # not part of the window asked for (a file that is not low does not count)
            report.append(None)
            continue
        if files is not None and len(report) - first >= files:
            break
        matches = find_matches(source, path, limit=limit)
        found = [describe_candidate(library, source, m, file or {"path": path}, album_id) for m in matches]
        if cover:
            compare_covers(source, settings, path, (file or {}).get("format"), matches, found)
        report.append({"path": path, "why": why, "now": label(album) if album else None, "candidates": found})
    return [entry for entry in report if entry is not None]


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
                        help="also compare the file's cover with the cover of each candidate (a cover the import already saved "
                             "is read from disk; any other is one request, so keep `--limit` small; needs OpenCV). The candidate whose cover is most like the file's "
                             "is marked")
    parser.add_argument("--json", action="store_true", help="JSON, for a script or an assistant")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    settings = context.settings()
    if settings is None:
        return 1
    if args.cover and not covers.opencv_installed():
        print("--cover needs OpenCV (`pip install opencv-python-headless`): the covers are not compared.", file=sys.stderr)
        args.cover = False
    with Library.for_settings(settings, readonly=True) as library:
        try:
            report = build_report(settings, library, args.query, low=args.low, limit=args.limit, cover=args.cover)
        except (UnknownSource, TypeError, NotReady) as err:
            print(err, file=sys.stderr)
            return 1
        except QueryError as err:
            return context.bad_query(err)
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
            for text in c["why_score"]:
                print(f"        = {text}", file=out)
            for text in c["for"]:
                print(f"        + {text}", file=out)
            for text in c["against"]:
                print(f"        - {text}", file=out)
    print(f"\n{len(report)} file(s). `*` is the album now linked. Redo one with `bdnex import --match --force FILE`.", file=out)
    return 0
