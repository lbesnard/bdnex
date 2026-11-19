"""``bdnex candidates``: for files whose match is in doubt, the other albums they could be, with the evidence."""

from __future__ import annotations

import argparse
import json
import logging
import os
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


def compare_covers(source, settings, path: str, fmt, matches, found: list[dict[str, Any]], step=None) -> None:
    """Add ``cover`` (0-100, or ``None`` when it could not be compared) to each candidate, and mark the best one.

    ``step(event, number, entry, url)`` says each step as it happens: ``download`` (before a request), then ``done``
    (the candidate has its ``cover``, or a ``cover_note`` saying why not)."""
    def say(event: str, number: int, entry: dict, url: str = "") -> None:
        if step:
            step(event, number, entry, url)

    local = covers.front_cover(path, fmt or "cbz")
    threshold = settings.cover.match_percentage
    for number, (match, entry) in enumerate(zip(matches, found), start=1):
        entry["cover"] = None
        if not local:
            entry["cover_note"] = "the file's first page could not be read"
            say("done", number, entry)
            continue
        known = source.library.find_album(source.name, match.candidate.source_id)
        album = source.library.get_album(known) if known else None
        url = (album or {}).get("cover_url") or source.guess_cover_url(match.candidate)
        logger.debug(f"{os.path.basename(path)}: cover of the candidate {entry.get('album', match.candidate.name)} ({url})")
        try:
            online = saved_cover(source, settings, match.candidate.source_id, album)  # the import already downloaded it
            entry["cover_from"] = "saved" if online is not None else "downloaded"
            if online is None:
                say("download", number, entry, url)
                online = source.cover_image(url)
            covers.check_cover_image(online)
            entry["cover"] = round(covers.sift_similarity(local, online))
        except (covers.CoverError, *source.fetch_errors) as err:
            logger.debug(f"{path}: cover of {url} not compared ({err})")
            entry["cover_note"] = f"not compared: {err}"
            say("done", number, entry)
            continue
        (entry["for"] if entry["cover"] >= threshold else entry["against"]).append(f"its cover is {entry['cover']}% like the file's")
        say("done", number, entry)
    scored = [e for e in found if e.get("cover") is not None]
    best = max(scored, key=lambda e: e["cover"], default=None)
    for entry in found:
        entry["cover_best"] = entry is best and best["cover"] >= threshold


class NotReady(RuntimeError):
    """The album list is not downloaded yet."""


def iter_report(settings, library, query: list[str], low: bool = False, limit: int = 5, cover: bool = False,
                first: int = 0, files: Optional[int] = None, progress=None, step=None):
    """The candidates of each file the query names, one entry at a time (so a command can show each as it is ready).

    ``progress(number, path, now, why)`` is called before the slow part of a file (the covers) and ``step`` (see
    ``compare_covers``) at each cover. Raises ``QueryError``,
    ``NotReady`` or the source's own errors."""
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
    seen = given = 0
    for path, album_id in sorted(work.items()):
        file = library.get_file(path)
        album = library.get_album(album_id) if album_id is not None else None
        why = is_low(settings, path, file, album, library.last_import(path)) if low else "asked for"
        if not why:
            continue
        if seen < first:  # not part of the window asked for (a file that is not low does not count)
            seen += 1
            continue
        if files is not None and given >= files:
            return
        seen += 1
        given += 1
        now = label(album) if album else None
        if progress:
            progress(given, path, now, why)
        matches = find_matches(source, path, limit=limit)
        found = [describe_candidate(library, source, m, file or {"path": path}, album_id) for m in matches]
        if cover:
            compare_covers(source, settings, path, (file or {}).get("format"), matches, found, step)
        yield {"path": path, "why": why, "now": now, "candidates": found}


def build_report(settings, library, query: list[str], low: bool = False, limit: int = 5, cover: bool = False,
                 first: int = 0, files: Optional[int] = None) -> list[dict[str, Any]]:
    """The candidates of each file the query names (the report of ``bdnex candidates --json``, also the web page's).

    ``first`` / ``files`` take a window of the (sorted) files: with ``--cover`` every file costs requests, so the
    web page asks for a few at a time. Raises ``QueryError``, ``NotReady`` or the source's own errors."""
    return list(iter_report(settings, library, query, low, limit, cover, first, files))


VERDICT_KIND = {"confirmed": "confirmed", "unverified": "unverified", "doubtful": "doubtful", "contradicted": "contradicted"}


def head_lines(number: int, path: str, now: Optional[str], why: str, on: bool) -> list[str]:
    """The lines that say which file is being looked at, and what it is linked to now."""
    from bdnex.ui import color

    return ["", f"{color.role(f'[{number}]', 'hint', on)} {color.role(os.path.basename(path), 'name', on)}",
            f"      {color.role(path, 'path', on)}",
            f"      now: {color.role(now, 'name', on) if now else color.role('(no album)', 'warn', on)}   "
            f"{color.role(f'[{why}]', 'note', on)}"]


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
    from bdnex.ui import color

    on = color.enabled(out)
    report = []
    with Library.for_settings(settings, readonly=True) as library:
        try:
            if args.json:  # the whole report at the end, nothing else on stdout
                report = build_report(settings, library, args.query, low=args.low, limit=args.limit, cover=args.cover)
            else:  # each file as it is ready: the file first (the covers take a while), then what was found for it
                def head(number, path, now, why):
                    print("\n".join(head_lines(number, path, now, why, on)), file=out, flush=True)

                def step(event, number, entry, url):
                    """Each cover as it is downloaded and compared: the album it belongs to, then what was found."""
                    if event == "download":
                        text = f"{number}. downloading the cover of {entry['album']}"
                        print(f"      {color.role(text, 'note', on)}  {color.role(url, 'path', on)}", file=out, flush=True)
                    else:
                        print("\n".join(candidate_lines(number, entry, on)), file=out, flush=True)

                for entry in iter_report(settings, library, args.query, low=args.low, limit=args.limit, cover=args.cover,
                                         progress=head, step=step):
                    report.append(entry)
                    if not args.cover:  # nothing slow happened: all the candidates at once
                        for number, c in enumerate(entry["candidates"], start=1):
                            print("\n".join(candidate_lines(number, c, on)), file=out, flush=True)
                    elif entry["candidates"]:
                        best = next((c for c in entry["candidates"] if c.get("cover_best")), None)
                        print("      " + (color.role(f"=> the cover of {best['album']} is the one most like the file's "
                                                      f"({best['cover']}%)", "good", on) if best else
                                         color.role("=> no candidate's cover looks like the file's", "warn", on)),
                              file=out, flush=True)
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
