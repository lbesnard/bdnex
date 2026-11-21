"""Check the doubtful matches a batch at a time, and remember each decision as it is made.

The batch is the next ``size`` files that need a look and have not been checked: a match something contradicts or nothing
confirms (``bdnex review --auto``'s verdicts, offline), and, when asked, the files that have no album. A decision is
written to the library at once (a ``checked`` mark, the flag, the link), so stopping at any moment loses nothing and the
next call continues with the files not checked yet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from bdnex.library import Library
from bdnex.library.recheck import UNCONFIRMED, WRONG, _cover_against_saved, weak_matches

CHECKED = "checked"  # the mark: keep | unlink | other:ID | leave
ACTIONS = ("keep", "unlink", "other", "leave")


@dataclass
class Row:
    path: str
    folder: str  # the last two folders of the path: they often say the series, the collection or the language
    album: Optional[str]  # what the file is linked to now (``None``: no album)
    album_id: Optional[int]
    album_url: Optional[str]
    score: Optional[float]  # the match score of the import that linked it
    cover: Optional[float]  # the cover match with the album's saved cover (0-100), when it could be told
    kind: str  # ``wrong`` | ``unconfirmed`` | ``unmatched`` | ``ok`` (nothing says it is doubtful: shown with ``everything``)
    why: list[str] = field(default_factory=list)  # what contradicts it, what confirms it, what is missing
    file_id: Optional[int] = None
    status: str = ""  # what was decided in this session: ``keep``, ``unlinked``, ``linked``, ``left``, ``skipped``
    message: str = ""  # what happened when it was decided (a refusal, for one)
    busy: bool = False  # a link is being made for it (the screen waits)
    link_id: int = 0  # bumped by every decision: the answer of a background job of an older one is ignored
    pending: bool = False  # linked at once; its page and cover are still to come
    facts: str = ""  # what the album says (year, credits, pages) next to what the file is: to judge a match by eye
    previous: str = ""  # an earlier decision (the ``checked`` mark), shown when files already checked are asked for again


def facts_of(album: Optional[dict], file: Optional[dict]) -> str:
    """One line each for the album (year, credits) and the file (pages, format, size): the two to compare by eye."""
    from bdnex.formatting import human_size
    from bdnex.ui.describe import credits_line

    lines = []
    if album:
        lines.append(" · ".join(part for part in (str(album["year"]) if album.get("year") else "", credits_line(album),
                                                  f"{album['pages']} pages" if album.get("pages") else "") if part))
    if file:
        lines.append("file: " + " · ".join(part for part in (
            (file.get("format") or "").upper(), f"{file['page_count']} pages" if file.get("page_count") else "",
            human_size(file["size"]) if file.get("size") else "") if part))
    return "\n".join(line for line in lines if line)


def folder_of(path: str, depth: int = 2) -> str:
    parts = os.path.dirname(path).split(os.sep)
    return os.sep.join(parts[-depth:]) if parts else ""


def is_checked(library: Library, file_id: Optional[int]) -> bool:
    return file_id is not None and CHECKED in library.attrs_of("file", file_id)


class Batch(list):
    """The rows of one batch, and ``total``: about how many files there are to check in all (a batch is ``size`` of them)."""

    total: int = 0


def next_batch(library: Library, settings, size: int = 50, terms: tuple[str, ...] | list[str] = (),
               unmatched: bool = False, everything: bool = False, again: bool = False,
               skip: frozenset[str] | set[str] = frozenset()) -> "Batch":
    """The next ``size`` files to check, in this order: the contradicted (lowest cover first), the unconfirmed, then, with
    ``unmatched`` (or ``everything``), the files without album, then, with ``everything``, all the other files of the query.
    Files already checked are left out unless ``again``, and so are the ``skip`` paths (the ones a screen already showed:
    a file passed over, or left without album, is not shown again by the next batch of the same run)."""
    from bdnex.ui.describe import label

    exclude = [] if again else [f"^attr:{CHECKED}"]
    verdicts = [v for v in weak_matches(library, settings, [*terms, *exclude], compare=False, store=False,
                                        flagged=True, weak=True) if v.kind in (WRONG, UNCONFIRMED) and v.path not in skip]
    verdicts.sort(key=lambda v: (v.kind != WRONG, v.cover if v.cover is not None else 101.0, v.path))
    extra = len(library.unmatched(list(terms))) if (unmatched or everything) else 0
    total = (len(library.query([*terms, *exclude], files=True)) if everything else len(verdicts)) + extra
    rows = Batch()
    rows.total = total
    for verdict in verdicts[:size]:
        file = library.get_file(verdict.path) or {}
        album = library.get_album(verdict.album_id) or {}
        cover = verdict.cover
        if cover is None:  # the saved cover of the album against the file's first page: offline, for these files only
            cover = _cover_against_saved(library, settings, verdict.path, album, file) if album else None
        why = [*(f"- {text}" for text in verdict.against), *(f"+ {text}" for text in verdict.for_)]
        if verdict.note:
            why.append(f"? {verdict.note}")
        if file.get("review"):
            why.insert(0, f"flagged: {file['review']}")
        rows.append(Row(verdict.path, folder_of(verdict.path), label(album) if album else verdict.album, verdict.album_id,
                        album.get("url"), verdict.score, cover, verdict.kind, why, file.get("id"),
                        facts=facts_of(album, file),
                        previous=library.attrs_of("file", file["id"]).get(CHECKED, "") if file.get("id") else ""))
    if (unmatched or everything) and len(rows) < size:
        for found in sorted(library.unmatched(list(terms)), key=lambda r: r["path"]):
            file = library.get_file(found["path"]) or {}
            previous = library.attrs_of("file", file["id"]).get(CHECKED, "") if file.get("id") else ""
            if (previous and not again) or found["path"] in skip:
                continue
            rows.append(Row(found["path"], folder_of(found["path"]), None, None, None, found.get("score"), None, "unmatched",
                            [f"last import: {found.get('outcome')}"], file.get("id"),
                            facts=facts_of(None, file), previous=previous))
            if len(rows) >= size:
                break
    if everything and len(rows) < size:
        taken = {row.path for row in rows}
        for found in sorted(library.query([*terms, *exclude], files=True), key=lambda r: r["path"]):
            if found["path"] in taken or found["path"] in skip or found.get("id") is None:
                continue
            file = library.get_file(found["path"]) or {}
            album = library.get_album(found["id"]) or {}
            last = library.last_import(found["path"])
            rows.append(Row(found["path"], folder_of(found["path"]), label(album) if album else None, found["id"],
                            album.get("url"), last["score"] if last else None, file.get("cover_score"), "ok", [],
                            file.get("id"), facts=facts_of(album, file),
                            previous=library.attrs_of("file", file["id"]).get(CHECKED, "") if file.get("id") else ""))
            if len(rows) >= size:
                break
    rank = {"wrong": 0, "unconfirmed": 1, "unmatched": 2, "ok": 3}  # in one folder, the files in most doubt first
    rows.sort(key=lambda row: (row.folder.casefold(), rank.get(row.kind, 4), row.cover if row.cover is not None else 101.0, row.path))
    return rows


def decide(library: Library, row: Row, action: str, album_id: Optional[int] = None) -> None:
    """Write a decision to the library now: ``keep`` (the flag goes), ``unlink`` (remembered as skipped, so ``import
    --skipped`` decides it again), ``other`` (``album_id``: the file is already linked to it, record that) or ``leave``."""
    from bdnex.ui.review_cmd import settle

    if row.file_id is None:
        row.file_id = (library.get_file(row.path) or {}).get("id")
    if action == "keep":
        settle(library, {"path": row.path}, "keep")
        value, row.status = "keep", "keep"
    elif action == "unlink":
        settle(library, {"path": row.path, "cover_score": row.cover}, "unlink")
        value, row.status = "unlink", "unlinked"
    elif action == "other":
        value, row.status = f"other:{album_id}", "linked"
    elif action == "leave":
        value, row.status = "leave", "left"
    else:
        raise ValueError(f"unknown decision {action!r}")
    if row.file_id is not None:
        library.set_attr("file", row.file_id, CHECKED, value)


def link_to(library: Library, row: Row, url: str, keep_both: bool = False) -> tuple[bool, str]:
    """Link the file to the album at ``url`` (as ``bdnex import --album --database-only`` does) and record it. A refusal
    (the album already has another file) changes nothing and says so."""
    from bdnex.ui import verdicts

    before = (library.get_file(row.path) or {}).get("album_id")
    verdicts.rematch(row.path, url, keep_both)
    return finish_link(library, row, before)


def link_now(library: Library, row: Row, candidate) -> int:
    """Write the link at once, before anything is fetched: the album from the address (a record of what the list says,
    when the library has none yet), the file linked to it (a spare version when it already has another file), the
    review flag gone, the decision saved. The page and the cover come later (``verdicts.rematch``, in the background)."""
    import os

    from bdnex.importer.bedetheque import parser
    from bdnex.ui.describe import label

    info = parser.partial_album_info(candidate)
    album_id = library.find_album(info.source, info.source_id)
    if album_id is None:
        album_id = library.add_album(info)
    others = [f for f in library.files_for_album(album_id) if f["path"] != os.path.abspath(row.path) and not f["spare"]]
    library.set_file_album(row.path, album_id)
    library.set_file_fields(row.path, spare=1 if others else 0)
    album = library.get_album(album_id) or {}
    row.album, row.album_id, row.album_url = label(album), album_id, album.get("url") or candidate.url
    row.facts = facts_of(album, library.get_file(row.path))
    row.cover, row.score, row.kind, row.why = None, None, "ok", ["+ chosen by you"]
    decide(library, row, "other", album_id)
    row.message = f"linked to {row.album}" + (" (a spare version: it has another file)" if others else "")
    return album_id


def record_manual(library: Library, row: Row, album_id: int) -> None:
    """The file was linked to an album written by hand (``library.manual``): the row shows it and the decision is saved."""
    from bdnex.ui.describe import label

    album = library.get_album(album_id) or {}
    row.album, row.album_id, row.album_url = label(album), album_id, None
    row.facts = facts_of(album, library.get_file(row.path))
    row.cover, row.score, row.kind, row.why = None, None, "ok", ["+ written by you"]
    decide(library, row, "other", album_id)
    row.message = f"linked to {row.album} (written by hand)"


def finish_link(library: Library, row: Row, before: Optional[int], url: Optional[str] = None) -> tuple[bool, str]:
    """After the import of ``link_to`` ran (maybe in another thread): did the file's album change? Record it if so."""
    from bdnex.ui.describe import label

    now = (library.get_file(row.path) or {}).get("album_id")
    already = url is not None and now is not None and ((library.get_album(now) or {}).get("url") or "") == url  # it was linked to it already
    if now is None or (now == before and not already):
        row.message = "refused: the album may already have another file (`bdnex duplicates`)"
        return False, row.message
    album = library.get_album(now) or {}
    row.album, row.album_id, row.album_url = label(album), now, album.get("url")
    row.facts = facts_of(album, library.get_file(row.path))
    decide(library, row, "other", now)
    row.message = f"linked to {row.album}"
    return True, row.message


def cover_files(library: Library, settings, row: Row):
    """``(path, format, saved cover path or None)``: what the cover popup shows (the library is read here, in the caller's thread)."""
    from bdnex.importer import covers

    file = library.get_file(row.path) or {}
    saved = None
    album = library.get_album(row.album_id) if row.album_id is not None else None
    if album:
        kept = album.get("cover_file")
        saved = (settings.cover_path / kept) if kept else None
        if saved is None or not saved.exists():
            found = covers.find_cover(settings.cover_path, album["source"], album["source_id"])
            saved = (settings.cover_path / found) if found else None
        if saved is not None and not saved.exists():
            saved = None
    return row.path, file.get("format") or os.path.splitext(row.path)[1].lstrip(".").lower(), saved


def read_covers(path: str, fmt: str, saved) -> tuple[Optional[bytes], Optional[bytes], str]:
    """The file's first page and the album's saved cover (no request, no library), and what could not be read."""
    from bdnex.importer import covers

    notes = []
    local = online = None
    try:
        local = covers.front_cover(path, fmt)
        if local is None:
            notes.append("the file's first page could not be read")
    except Exception as err:  # a disk not mounted, a damaged archive
        notes.append(f"the file's cover could not be read ({err})")
    if saved is None:
        notes.append("the album's cover is not saved (`bdnex import --fetch` or `candidates --cover` saves it)")
    else:
        try:
            online = saved.read_bytes()
        except OSError as err:
            notes.append(f"the album's cover could not be read ({err})")
    return local, online, "; ".join(notes)


class NoVolumeFound(Exception):
    """A series page was read, but none of the files finds its volume in it: the message says what each side has."""


def read_series_address(settings, address: str):
    """``(series id, name, albums)`` of a series page address, or ``None``: the page from the library, or fetched once with the
    configured fetcher and stored. Its own writable library and source: the screen's album list is offline and read-only.
    Slow when it fetches: run it in a thread."""
    from bdnex.importer.registry import create_source

    with Library.for_settings(settings) as library:
        source = create_source(settings.source, library, settings)
        return source.series_by_address(address) if hasattr(source, "series_by_address") else None


def propose_for_series_address(source, address: str, paths: list[str], found=None):
    """The album of the series page at ``address`` (``bedetheque.com/serie-N-BD-Name.html``) for each of ``paths``, by the volume
    number in the file name; ``None`` when it is not a series page or no file finds its volume. May read the page from the site
    once (run it in the thread that owns the source). ``found``: what ``read_series_address`` returned, when it was read apart."""
    from bdnex.importer import folders

    if found is None:
        found = source.series_by_address(address) if hasattr(source, "series_by_address") else None
    if found is None:
        return None
    key, name, albums = found
    volumes = {path: folders.title_and_volume(path)[1] for path in paths}
    by_volume = {}
    for album in albums:
        if album.volume is not None:
            by_volume.setdefault(album.volume, album)
    proposal = folders.SeriesProposal(name, key, with_volume=sum(v is not None for v in volumes.values()), total=len(paths))
    for path, volume in volumes.items():
        if volume in by_volume:
            proposal.mapping[path] = by_volume[volume]
    if not proposal.mapping:
        listed = sorted(by_volume)
        raise NoVolumeFound(
            f"{name}: its page lists {len(albums)} album(s), " + (f"with the volumes {', '.join(map(str, listed[:12]))}" if listed else
            "none with a volume number (collected editions, `INT`, or one-shots?)")
            + "; the files ask for " + (", ".join(str(v) for v in sorted({v for v in volumes.values() if v is not None})[:12]) or "no volume") + ".")
    return proposal


def propose_for_series(source, candidate, paths: list[str]):
    """The album of the series of ``candidate`` for each of ``paths`` (by the volume number in the file name): a
    ``SeriesProposal`` (``mapping`` file -> album), or ``None`` when the album has no series or no file names a volume.
    Reads the album list only (run it in the thread that owns the source)."""
    from bdnex.importer import folders

    series = folders._series_of(source, candidate)
    if series is None:
        return None
    return folders.propose_for_series(source, series.source_id, series.name, paths, {})
