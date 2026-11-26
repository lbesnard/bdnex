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

LIMIT = 50  # rows to a page by default (the screen passes its batch size): `n` and `b` turn the pages
NONE = "(none)"

# -- the two ideas a file has: how it was linked (bdnex's) and whether you confirmed it (yours), and the colour they make
CONFIRMED_SQL = "EXISTS (SELECT 1 FROM file_attrs x WHERE x.file_id = f.id AND x.key = 'checked' AND x.value != 'unlink')"  # (an unlink is not a confirmation)
SCORE_SQL = "(SELECT score FROM import_log il WHERE il.path = f.path ORDER BY il.id DESC LIMIT 1)"
LINKS = (("auto matched", "a strong match with nothing against it"), ("medium match", "applied, but the score is not strong"),
         ("doubtful", "the match is flagged: something is against it"), ("no album", "linked to no album"),
         ("manual", "an album you wrote by hand"))
COLOURS = (("green", "confirmed: you decided (kept, linked, or no album online)"), ("cyan", "auto matched, not confirmed yet"),
           ("orange", "medium match, not confirmed yet"), ("red", "doubtful, or no album, not confirmed: look here first"))
REVIEWS = (("confirmed", "you decided"), ("not confirmed", "not yet"), ("no album online", "confirmed with no album: nothing exists online for it"))
OTHERS = (("duplicates", "several files for one album"), ("versions", "a version or child of another album"), ("damaged", "the file is damaged"))
DESCRIBED = {name: text for group in (LINKS, COLOURS, REVIEWS, OTHERS) for name, text in group}
OTHER_SQL = {"duplicates": "(f.spare = 1 OR f.album_id IN (SELECT album_id FROM files WHERE album_id IS NOT NULL GROUP BY album_id HAVING COUNT(*) > 1))",
             "versions": "a.relation IS NOT NULL", "damaged": "f.corrupted = 1"}
JOINS = "FROM files f LEFT JOIN albums a ON a.id = f.album_id LEFT JOIN series s ON s.id = a.series_id"
GROUPS = ("Colour", "Link", "Review", "Other", "Kind", "Category", "Collection", "Genre", "Author", "Series", "Publisher", "Format", "Language")
COLOUR_OF = {"auto matched": "cyan", "manual": "cyan", "medium match": "orange", "doubtful": "red", "no album": "red"}  # for a file not confirmed


def strong_of(settings) -> float:
    return float(getattr(getattr(settings, "match", None), "strong_threshold", 90))


def link_sql(strong: float) -> str:
    """How the file was linked: ``no album``, ``manual``, ``doubtful`` (flagged), ``auto matched`` (the score of the import is strong) or ``medium match``."""
    return (f"(CASE WHEN f.album_id IS NULL THEN 'no album' WHEN a.source = 'manual' THEN 'manual' WHEN f.review IS NOT NULL THEN 'doubtful' "
            f"WHEN COALESCE({SCORE_SQL}, 0) >= {float(strong)} THEN 'auto matched' ELSE 'medium match' END)")


def level_sql(strong: float) -> str:
    """The colour of a file: green when you confirmed it, else by how it was linked (cyan auto matched or manual, orange medium, red doubtful or no album)."""
    return (f"(CASE WHEN {CONFIRMED_SQL} THEN 'green' WHEN {link_sql(strong)} IN ('auto matched', 'manual') THEN 'cyan' "
            f"WHEN {link_sql(strong)} = 'medium match' THEN 'orange' ELSE 'red' END)")


def file_levels(library, strong: float) -> list[tuple[int, str, str]]:
    """``[(file id, stored path, colour)]`` for every file: one query."""
    return [(r[0], r[1], r[2]) for r in library.conn.execute(f"SELECT f.id, f.path, {level_sql(strong)} {JOINS}")]


def _text_condition(group: str, value: str, strong: float = 90.0) -> tuple[str, list]:
    none = value == NONE
    simple = {"Kind": "a.kind", "Category": "a.category", "Collection": "a.collection", "Language": "a.language", "Series": "s.name"}
    if group == "Colour":
        if value not in dict(COLOURS):
            raise ValueError(f"no colour {value!r}")
        return f"{level_sql(strong)} = '{value}'", []
    if group == "Link":
        return f"{link_sql(strong)} = ?", [value]
    if group == "Review":
        return {"confirmed": CONFIRMED_SQL, "not confirmed": f"NOT {CONFIRMED_SQL}",
                "no album online": f"({CONFIRMED_SQL} AND f.album_id IS NULL)"}[value], []
    if group == "Other":
        return OTHER_SQL[value], []
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


def facets(library, strong: float = 90.0) -> dict[str, list[tuple[str, int]]]:
    """``{group: [(value, files)]}``: what the Filters tree lists, most files first inside a group (names in order for the long ones)."""
    conn = library.conn
    found: dict[str, list[tuple[str, int]]] = {}
    counts: dict[tuple, int] = {}
    for link, confirmed, album, n in conn.execute(f"SELECT {link_sql(strong)}, {CONFIRMED_SQL}, f.album_id IS NULL, COUNT(*) {JOINS} GROUP BY 1, 2, 3"):
        counts[(link, bool(confirmed), bool(album))] = n
    total = lambda pick: sum(n for key, n in counts.items() if pick(*key))  # noqa: E731
    found["Colour"] = [(name, total(lambda link, ok, none, name=name: (ok and name == "green") or (not ok and COLOUR_OF[link] == name)))
                       for name, _ in COLOURS]
    found["Link"] = [(name, total(lambda link, ok, none, name=name: link == name)) for name, _ in LINKS]
    found["Review"] = [("confirmed", total(lambda link, ok, none: ok)), ("not confirmed", total(lambda link, ok, none: not ok)),
                       ("no album online", total(lambda link, ok, none: ok and none))]
    found["Other"] = [(name, conn.execute(f"SELECT COUNT(*) {JOINS} WHERE {OTHER_SQL[name]}").fetchone()[0]) for name, _ in OTHERS]
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


def plan_counts(planned: dict[int, str], levels: dict[int, str]) -> dict[str, list[int]]:
    """``{planned folder: [files, confirmed, red, orange, cyan]}``, the shape ``folders.build`` reads: the tree of where a move would put things."""
    counts: dict[str, list[int]] = {}
    slot = {"green": 1, "red": 2, "orange": 3, "cyan": 4}
    for file_id, dest in planned.items():
        entry = counts.setdefault(os.path.dirname(dest), [0, 0, 0, 0, 0])
        entry[0] += 1
        entry[slot.get(levels.get(file_id, "cyan"), 4)] += 1
    return counts


def checked_ids(library) -> set[int]:
    return {r[0] for r in library.conn.execute("SELECT file_id FROM file_attrs WHERE key = ? AND value != 'unlink'", (CHECKED,))}


def _label(row) -> Optional[str]:
    from bdnex.ui.describe import label

    if row["album_id"] is None:
        return None
    return label({"series": row["series"], "volume": row["volume"], "alternate_number": row["alternate_number"], "title": row["title"]})


def _rows(library, rows, planned: Optional[dict[int, str]]) -> Batch:
    out = Batch()
    for r in rows:
        path = library._resolve(r["path"])
        kind = "unmatched" if r["album_id"] is None else ("unconfirmed" if (r["review"] or r["link"] == "medium match") else "ok")
        why = [f"flagged: {r['review']}"] if r["review"] else ([] if r["album_id"] is not None else ["no album"])
        row = Row(path, folder_of(path), _label(r), r["album_id"], r["url"], r["score"], r["cover_score"], kind, why, r["id"],
                  previous=r["checked"] or "", dest=(planned or {}).get(r["id"], ""), album_kind=r["kind"] or "", volume=r["volume"],
                  category=r["category"] or "", spare=bool(r["spare"]), link=r["link"])
        row.facts = f"file: {(r['format'] or '').upper()} · {r['page_count'] or '?'} pages" + (" · spare version" if r["spare"] else "")
        out.append(row)
    return out


def select_sql(strong: float) -> str:
    return (f"SELECT f.id, f.path, f.album_id, f.review, f.cover_score, f.spare, f.corrupted, f.format, f.page_count, "
            "a.kind, a.volume, a.title, a.alternate_number, a.category, a.url, s.name AS series, "
            f"(SELECT value FROM file_attrs x WHERE x.file_id = f.id AND x.key = 'checked') AS checked, {SCORE_SQL} AS score, "
            f"{link_sql(strong)} AS link {JOINS}")


def prefix_range(folder: str) -> tuple[str, str]:
    """The stored paths below a folder are exactly those from ``folder/`` up to (not including) ``folder0``: an index range, not a scan."""
    low = folder.rstrip("/") + "/"
    return low, low[:-1] + chr(ord("/") + 1)


def rows_for(library, settings, kind: str, value: Any, planned: Optional[dict[int, str]] = None, limit: int = LIMIT, offset: int = 0) -> Batch:
    """The rows of one selection (``limit`` of them from ``offset``, in path order); ``.total`` is how many there are in all."""
    return rows_for_many(library, settings, [(kind, value)], planned, limit, offset)


def _planned_rows(library, ids: list[int], planned: dict[int, str], limit: int, offset: int = 0, strong: float = 90.0) -> Batch:
    """The files at ``ids``, in the order of where a move would put them (``limit`` of them from ``offset``)."""
    ids = sorted(ids, key=lambda i: planned[i].casefold())
    page = ids[offset:offset + limit]
    rows = []
    for start in range(0, len(page), 500):
        chunk = page[start:start + 500]
        rows += library.conn.execute(f"{select_sql(strong)} WHERE f.id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    order = {i: n for n, i in enumerate(page)}
    rows.sort(key=lambda r: order[r["id"]])
    batch = _rows(library, rows, planned)
    batch.total = len(ids)
    return batch


def rows_for_many(library, settings, selections: list, planned: Optional[dict[int, str]] = None, limit: int = LIMIT, offset: int = 0) -> Batch:
    """The files of several selections: folders (and organised folders) are alternatives, the values of one filter group are alternatives, and
    different kinds go together (these folders AND this genre AND not reviewed yet)."""
    conn = library.conn
    strong = strong_of(settings)
    folders = [value for kind, value in selections if kind == "folder"]
    moves = [value for kind, value in selections if kind == "planned"]
    groups: dict[str, list] = {}
    for kind, value in selections:
        if kind == "facet":
            groups.setdefault(value[0], []).append(value[1])
        elif kind not in ("folder", "planned"):
            raise ValueError(f"no selection {kind!r}")
    parts: list[str] = []
    params: list = []
    if folders and "" not in folders:
        ranges = []
        for folder in folders:
            low, high = prefix_range(folder)
            ranges.append("(f.path >= ? AND f.path < ?)")
            params += [low, high]
        parts.append("(" + " OR ".join(ranges) + ")")
    if moves:
        prefixes = [move.rstrip("/") + "/" if move else "" for move in moves]
        ids = [i for i, dest in (planned or {}).items() if any(not prefix or dest.startswith(prefix) for prefix in prefixes)]
        if not parts and not groups:
            return _planned_rows(library, ids, planned or {}, limit, offset, strong)  # only organised folders: in the order a move would give
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS plansel(id INTEGER PRIMARY KEY)")
        conn.execute("DELETE FROM plansel")
        conn.executemany("INSERT INTO plansel(id) VALUES (?)", [(i,) for i in ids])
        parts.append("f.id IN (SELECT id FROM plansel)")
    for group, values in groups.items():
        conditions = []
        for value in values:
            condition, extra = _text_condition(group, value, strong)
            conditions.append(condition)
            params += extra
        parts.append("(" + " OR ".join(conditions) + ")")
    where = " AND ".join(parts) or "1"
    total = conn.execute(f"SELECT COUNT(*) {JOINS} WHERE {where}", params).fetchone()[0]
    rows = conn.execute(f"{select_sql(strong)} WHERE {where} ORDER BY f.path LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
    batch = _rows(library, rows, planned)
    batch.total = total
    return batch
