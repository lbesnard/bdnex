"""Find files fast, by folder, by where `bdnex move` would put them, or by a facet (state, kind, category, genre, author, series...).

Everything the review screen's left panel shows comes from here, in one query each (the indexes of schema v17 make a facet a lookup):

* :func:`rows_for`: the rows of a selection, built by one join, no file read and no page parsed: a few thousand rows in well under a second.
* :func:`plan`: the destination of every file (a template render is ~0.1 ms, so 17 000 files take ~2 s: run it in the background).
* :func:`facets`: the groups of the Filters tree and their counts.

A selection is ``(kind, value)``: ``("folder", "Series/Tintin")`` (everything below it), ``("planned", "Series/Tintin")`` (everything a move would
put below it), ``("facet", (group, value))``.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from bdnex.library.review_batch import CHECKED, Batch, Row, folder_of

LIMIT = 1000  # rows drawn at once: the table is fast to fill up to this, and a screen can not show more anyway; the filters narrow it
NONE = "(none)"

STATES = (("needs attention", "not reviewed, and no album or a doubtful match"), ("unreviewed", "not reviewed yet"), ("reviewed", "reviewed"), ("linked", "linked to an album"), ("unmatched", "no album"),
          ("doubtful", "flagged: the match is doubtful"), ("duplicates", "several files for one album"), ("versions", "a version or child of another album"),
          ("damaged", "the file is damaged"))
CHECKED_SQL = "EXISTS (SELECT 1 FROM file_attrs x WHERE x.file_id = f.id AND x.key = 'checked')"
STATE_SQL = {
    "needs attention": f"(NOT {CHECKED_SQL} AND (f.album_id IS NULL OR f.review IS NOT NULL))", "unreviewed": f"NOT {CHECKED_SQL}", "reviewed": CHECKED_SQL, "linked": "f.album_id IS NOT NULL", "unmatched": "f.album_id IS NULL",
    "doubtful": "f.review IS NOT NULL",
    "duplicates": "(f.spare = 1 OR f.album_id IN (SELECT album_id FROM files WHERE album_id IS NOT NULL GROUP BY album_id HAVING COUNT(*) > 1))",
    "versions": "a.relation IS NOT NULL", "damaged": "f.corrupted = 1"}
JOINS = "FROM files f LEFT JOIN albums a ON a.id = f.album_id LEFT JOIN series s ON s.id = a.series_id"
GROUPS = ("State", "Kind", "Category", "Collection", "Genre", "Author", "Series", "Publisher", "Format", "Language")


def _text_condition(group: str, value: str) -> tuple[str, list]:
    none = value == NONE
    simple = {"Kind": "a.kind", "Category": "a.category", "Collection": "a.collection", "Language": "a.language", "Series": "s.name"}
    if group == "State":
        return STATE_SQL[value], []
    if group in simple:
        return (f"({simple[group]} IS NULL AND a.id IS NOT NULL)", []) if none else (f"{simple[group]} = ?", [value])
    if group == "Format":
        return "f.format = ?", [value]
    if group == "Publisher":
        return "a.publisher_id IN (SELECT id FROM publishers WHERE name = ?)", [value]
    if group == "Genre":
        return "f.album_id IN (SELECT ag.album_id FROM album_genres ag JOIN genres g ON g.id = ag.genre_id WHERE g.name = ?)", [value]
    if group == "Author":
        return "f.album_id IN (SELECT c.album_id FROM album_credits c JOIN people p ON p.id = c.person_id WHERE p.name = ?)", [value]
    raise ValueError(f"no filter {group!r}")


def facets(library) -> dict[str, list[tuple[str, int]]]:
    """``{group: [(value, files)]}``: what the Filters tree lists, most files first inside a group (names in order for the long ones)."""
    conn = library.conn
    found: dict[str, list[tuple[str, int]]] = {}
    found["State"] = [(name, conn.execute(f"SELECT COUNT(*) {JOINS} WHERE {STATE_SQL[name]}").fetchone()[0]) for name, _ in STATES]
    rows = lambda sql: [(r[0], r[1]) for r in conn.execute(sql) if r[0] is not None]  # noqa: E731
    found["Kind"] = rows(f"SELECT a.kind, COUNT(*) {JOINS} WHERE a.id IS NOT NULL GROUP BY a.kind ORDER BY 2 DESC")
    found["Category"] = rows(f"SELECT COALESCE(a.category, '{NONE}'), COUNT(*) {JOINS} WHERE a.id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
    found["Collection"] = rows(f"SELECT a.collection, COUNT(*) {JOINS} WHERE a.collection IS NOT NULL GROUP BY 1 ORDER BY 1 COLLATE NOCASE")
    found["Genre"] = rows("SELECT g.name, COUNT(DISTINCT f.id) FROM files f JOIN album_genres ag ON ag.album_id = f.album_id "
                          "JOIN genres g ON g.id = ag.genre_id GROUP BY g.name ORDER BY 2 DESC")
    found["Author"] = rows("SELECT p.name, COUNT(DISTINCT f.id) FROM files f JOIN album_credits c ON c.album_id = f.album_id "
                           "JOIN people p ON p.id = c.person_id GROUP BY p.name ORDER BY p.name COLLATE NOCASE")
    found["Series"] = rows(f"SELECT s.name, COUNT(*) {JOINS} WHERE s.id IS NOT NULL GROUP BY s.name ORDER BY s.name COLLATE NOCASE")
    found["Publisher"] = rows("SELECT p.name, COUNT(*) FROM files f JOIN albums a ON a.id = f.album_id JOIN publishers p ON p.id = a.publisher_id "
                              "GROUP BY p.name ORDER BY 2 DESC")
    found["Format"] = rows("SELECT f.format, COUNT(*) FROM files f GROUP BY f.format ORDER BY 2 DESC")
    found["Language"] = rows(f"SELECT a.language, COUNT(*) {JOINS} WHERE a.language IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
    return found


def plan(library, settings, only: Optional[set[int]] = None) -> dict[int, str]:
    """``{file id: where `bdnex move` would put it}``, relative to the collection and with the extension it would have. Offline and read-only;
    a file the template refuses or that needs a question (a missing field) is left out. ``only``: the album ids to work out (after an edit)."""
    from bdnex.organizer.organizer import FileResult, Organizer, _Deferring

    organizer = Organizer(library, settings, prompter=_Deferring(), pretend=True)
    root = str(settings.require_collection())
    found: dict[int, str] = {}
    sql = "SELECT id, path, format, album_id, spare FROM files WHERE album_id IS NOT NULL"
    for row in library.conn.execute(sql).fetchall():
        if only is not None and row["album_id"] not in only:
            continue
        try:
            dest = organizer._destination(library._resolve(row["path"]), row["format"] or "cbz", row["album_id"], "move", bool(row["spare"]))
        except Exception:
            continue
        if isinstance(dest, FileResult) or not isinstance(dest, str):
            continue
        found[row["id"]] = os.path.relpath(dest, root) if dest.startswith(root) else dest
    return found


def plan_counts(planned: dict[int, str], checked: set[int]) -> dict[str, list[int]]:
    """``{planned folder: [files, reviewed]}``, the shape ``folders.build`` reads: the tree of where a move would put things."""
    counts: dict[str, list[int]] = {}
    for file_id, dest in planned.items():
        entry = counts.setdefault(os.path.dirname(dest), [0, 0])
        entry[0] += 1
        entry[1] += file_id in checked
    return counts


def checked_ids(library) -> set[int]:
    return {r[0] for r in library.conn.execute("SELECT file_id FROM file_attrs WHERE key = ?", (CHECKED,))}


def _label(row) -> Optional[str]:
    from bdnex.ui.describe import label

    if row["album_id"] is None:
        return None
    return label({"series": row["series"], "volume": row["volume"], "alternate_number": row["alternate_number"], "title": row["title"]})


def _rows(library, rows, planned: Optional[dict[int, str]]) -> Batch:
    out = Batch()
    for r in rows:
        path = library._resolve(r["path"])
        kind = "unmatched" if r["album_id"] is None else ("unconfirmed" if r["review"] else "ok")
        why = [f"flagged: {r['review']}"] if r["review"] else ([] if r["album_id"] is not None else ["no album"])
        row = Row(path, folder_of(path), _label(r), r["album_id"], r["url"], r["score"], r["cover_score"], kind, why, r["id"],
                  previous=r["checked"] or "", dest=(planned or {}).get(r["id"], ""), album_kind=r["kind"] or "", volume=r["volume"],
                  category=r["category"] or "", spare=bool(r["spare"]))
        row.facts = f"file: {(r['format'] or '').upper()} · {r['page_count'] or '?'} pages" + (" · spare version" if r["spare"] else "")
        out.append(row)
    return out


SELECT = (f"SELECT f.id, f.path, f.album_id, f.review, f.cover_score, f.spare, f.corrupted, f.format, f.page_count, "
          "a.kind, a.volume, a.title, a.alternate_number, a.category, a.url, s.name AS series, "
          "(SELECT value FROM file_attrs x WHERE x.file_id = f.id AND x.key = 'checked') AS checked, "
          "(SELECT score FROM import_log il WHERE il.path = f.path ORDER BY il.id DESC LIMIT 1) AS score "
          f"{JOINS}")


def prefix_range(folder: str) -> tuple[str, str]:
    """The stored paths below a folder are exactly those from ``folder/`` up to (not including) ``folder0``: an index range, not a scan."""
    low = folder.rstrip("/") + "/"
    return low, low[:-1] + chr(ord("/") + 1)


def rows_for(library, settings, kind: str, value: Any, planned: Optional[dict[int, str]] = None, limit: int = LIMIT) -> Batch:
    """The rows of a selection (at most ``limit``, in path order); ``.total`` is how many there are in all."""
    conn = library.conn
    if kind == "folder":
        if value:
            low, high = prefix_range(value)
            where, params = "f.path >= ? AND f.path < ?", [low, high]
        else:
            where, params = "1", []
    elif kind == "facet":
        where, params = _text_condition(*value)
    elif kind == "planned":
        ids = sorted((i for i, dest in (planned or {}).items() if not value or dest.startswith(value.rstrip("/") + "/")),
                     key=lambda i: planned[i].casefold())
        total = len(ids)
        rows = []
        for start in range(0, min(len(ids), limit), 500):
            chunk = ids[start:start + 500][:limit - start]
            rows += conn.execute(f"{SELECT} WHERE f.id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
        order = {i: n for n, i in enumerate(ids)}
        rows.sort(key=lambda r: order[r["id"]])
        batch = _rows(library, rows, planned)
        batch.total = total
        return batch
    else:
        raise ValueError(f"no selection {kind!r}")
    total = conn.execute(f"SELECT COUNT(*) {JOINS} WHERE {where}", params).fetchone()[0]
    rows = conn.execute(f"{SELECT} WHERE {where} ORDER BY f.path LIMIT ?", [*params, limit]).fetchall()
    batch = _rows(library, rows, planned)
    batch.total = total
    return batch
