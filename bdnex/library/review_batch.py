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
    kind: str  # ``wrong`` | ``unconfirmed`` | ``unmatched``
    why: list[str] = field(default_factory=list)  # what contradicts it, what confirms it, what is missing
    file_id: Optional[int] = None
    status: str = ""  # what was decided in this session: ``keep``, ``unlinked``, ``linked``, ``left``, ``skipped``
    message: str = ""  # what happened when it was decided (a refusal, for one)


def folder_of(path: str, depth: int = 2) -> str:
    parts = os.path.dirname(path).split(os.sep)
    return os.sep.join(parts[-depth:]) if parts else ""


def is_checked(library: Library, file_id: Optional[int]) -> bool:
    return file_id is not None and CHECKED in library.attrs_of("file", file_id)


def next_batch(library: Library, settings, size: int = 50, terms: tuple[str, ...] | list[str] = (),
               unmatched: bool = False) -> list[Row]:
    """The next ``size`` files to check (most doubtful first: contradicted, then unconfirmed, then without album)."""
    verdicts = [v for v in weak_matches(library, settings, [*terms, f"^attr:{CHECKED}"], compare=False, store=False,
                                        flagged=True, weak=True) if v.kind in (WRONG, UNCONFIRMED)]
    verdicts.sort(key=lambda v: (v.kind != WRONG, v.cover if v.cover is not None else 101.0, v.path))
    rows = []
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
        from bdnex.ui.describe import label

        rows.append(Row(verdict.path, folder_of(verdict.path), label(album) if album else verdict.album, verdict.album_id,
                        album.get("url"),
                        verdict.score, cover, verdict.kind, why, file.get("id")))
    if unmatched and len(rows) < size:
        for found in sorted(library.unmatched(list(terms)), key=lambda r: r["path"]):
            file = library.get_file(found["path"]) or {}
            if is_checked(library, file.get("id")):
                continue
            rows.append(Row(found["path"], folder_of(found["path"]), None, None, None, found.get("score"), None, "unmatched",
                            [f"last import: {found.get('outcome')}"], file.get("id")))
            if len(rows) >= size:
                break
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


def link_to(library: Library, row: Row, url: str) -> tuple[bool, str]:
    """Link the file to the album at ``url`` (as ``bdnex import --album --database-only`` does) and record it. A refusal
    (the album already has another file) changes nothing and says so."""
    from bdnex.ui import verdicts

    before = (library.get_file(row.path) or {}).get("album_id")
    verdicts.rematch(row.path, url)
    return finish_link(library, row, before)


def finish_link(library: Library, row: Row, before: Optional[int]) -> tuple[bool, str]:
    """After the import of ``link_to`` ran (maybe in another thread): did the file's album change? Record it if so."""
    from bdnex.ui.describe import label

    now = (library.get_file(row.path) or {}).get("album_id")
    if now is None or now == before:
        row.message = "refused: the album may already have another file (`bdnex duplicates`)"
        return False, row.message
    album = library.get_album(now) or {}
    row.album, row.album_id, row.album_url = label(album), now, album.get("url")
    decide(library, row, "other", now)
    row.message = f"linked to {row.album}"
    return True, row.message
