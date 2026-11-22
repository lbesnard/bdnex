"""What ``bdnex candidates`` finds, without printing anything: the other albums a file could be, with the evidence.

The command (``ui/candidates_cmd.py``), ``bdnex review --auto`` (``ui/verdicts.py``), the search of the album list and the web page's
candidates view (``GET /api/candidates``) all call these functions, so none of them depends on a command's module.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from bdnex.importer import confirm, covers
from bdnex.importer.matching import find_matches
from bdnex.importer.registry import offline_settings, create_source
from bdnex.ui.describe import label

logger = logging.getLogger(__name__)


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


def download_cover(source, candidate, url: str, guessed: bool, say=lambda real: None) -> bytes:
    """The picture at ``url``. When that address was only a guess (the album's page was never read) and the site has no such
    picture, the page says where the cover really is: read it (one request, the page is kept) and try that address."""
    try:
        return source.cover_image(url)
    except source.fetch_errors as err:
        real = None
        if guessed and hasattr(source, "cover_url_of"):
            try:
                real = source.cover_url_of(candidate)
            except Exception as inner:  # a page that does not come: the first error is the one to report
                logger.debug(f"{candidate.url}: the page that names its cover did not come ({inner})")
        if not real or real == url:
            raise err
        logger.debug(f"{url} is only a guess and is not there; the page of {candidate.url} says {real}")
        say(real)
        return source.cover_image(real)


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
                online = download_cover(source, match.candidate, url, guessed=not (album or {}).get("cover_url"),
                                        say=lambda real: say("download", number, entry, real))
            else:
                say("saved", number, entry, url)
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
                first: int = 0, files: Optional[int] = None, progress=None, step=None, announce=None):
    """The candidates of each file the query names, one entry at a time (so a command can show each as it is ready).

    ``announce(None)`` is called before the files are chosen and ``announce(total)`` after; ``progress(number, total,
    path, now, why)`` before the slow part of a file (the covers) and ``step`` (see ``compare_covers``) at each cover.
    Raises ``QueryError``, ``NotReady`` or the source's own errors."""
    offline = settings if cover else offline_settings(settings)
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
    if announce:
        announce(None)  # the filter below can take a while on a big library
    todo = []  # the files to look at, known before the first one is: so that every file can say `[n/total]`
    for path, album_id in sorted(work.items()):
        file = library.get_file(path)
        album = library.get_album(album_id) if album_id is not None else None
        why = is_low(settings, path, file, album, library.last_import(path)) if low else "asked for"
        if why:
            todo.append((path, album_id, file, album, why))
    todo = todo[first:] if files is None else todo[first:first + files]
    if announce:
        announce(len(todo))
    for given, (path, album_id, file, album, why) in enumerate(todo, start=1):
        now = label(album) if album else None
        if progress:
            progress(given, len(todo), path, now, why)
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
