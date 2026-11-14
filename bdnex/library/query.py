"""Query language for ``bdnex ls`` (a small subset of the beets syntax).

Each argument is one term; all terms must match (AND)::

    astérix                  free text: title, series, authors or file name
    series:nains             case- and accent-insensitive substring
    series:=Nains            exact (case/accent-insensitive)
    title::^Les.*Nains       regular expression (also  title:/^Les.*Nains/ )
    any:soleil               any text field at once: title, series, publisher, credits, genres, summary, file name...
    ::^13m                   a regular expression on any text field (also  /^13m/ )
    year:2010..2015          numeric range (open ends allowed: 2010.. / ..2015)
    volume:3   rating:4..
    author:uderzo            any credit;  writer: penciller: colorist: ...
    genre:fantasy  publisher:soleil  kind:oneshot
    filename:redwin  format:cbz  corrupted:true  complete:false
    ^genre:humour            a leading ^ negates the term
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

ROLES = {"writer": "Writer", "penciller": "Penciller", "inker": "Inker", "colorist": "Colorist",
         "letterer": "Letterer", "coverartist": "CoverArtist"}


class QueryError(ValueError):
    """The query text is invalid."""


def fold(value: Any) -> str:
    """Lowercase and strip accents, so ``astérix`` matches ``Asterix``."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def regexp(pattern: str, value: Any) -> bool:
    """Case- and accent-insensitive search. The pattern keeps its escapes (``\\D`` stays ``\\D``)."""
    if value is None:
        return False
    return re.search(strip_accents(pattern), strip_accents(str(value)), re.IGNORECASE) is not None


def register_functions(conn) -> None:
    conn.create_function("fold", 1, fold, deterministic=True)
    conn.create_function("regexp", 2, regexp, deterministic=True)
    conn.create_function("basename", 1, lambda p: (p or "").rsplit("/", 1)[-1], deterministic=True)


# field -> SQL expression (album mode and file mode share the same joins: a, s, pub, f)
TEXT_FIELDS = {"title": "a.title", "series": "s.name", "publisher": "pub.name", "isbn": "a.isbn",
               "language": "a.language", "summary": "a.summary", "kind": "a.kind",
               "url": "a.url", "source": "a.source"}
FILE_TEXT_FIELDS = {"path": "f.path", "filename": "basename(f.path)", "format": "f.format", "md5": "f.md5",
                    "embedded": "COALESCE(f.embedded, '')"}
NUMERIC_FIELDS = {"id": "a.id", "year": "a.year", "volume": "a.volume", "pages": "a.pages", "rating": "a.rating"}
FILE_NUMERIC_FIELDS = {"fileid": "f.id", "size": "f.size", "filepages": "f.page_count", "cover": "f.cover_score"}
BOOL_FIELDS = {"partial": "COALESCE(json_extract(a.extra, '$.partial'), 0)",
               "complete": "(1 - COALESCE(json_extract(a.extra, '$.partial'), 0))"}  # its page was read
FILE_BOOL_FIELDS = {"corrupted": "f.corrupted", "tagged": "(f.album_id IS NOT NULL)",
                    "metadata": "f.has_metadata", "spare": "f.spare", "review": "(f.review IS NOT NULL)",
                    "staged": "EXISTS (SELECT 1 FROM staged_files sf WHERE sf.file_id = f.id)"}

ALL_FIELDS = sorted({*TEXT_FIELDS, *FILE_TEXT_FIELDS, *NUMERIC_FIELDS, *FILE_NUMERIC_FIELDS, *BOOL_FIELDS,
                     *FILE_BOOL_FIELDS, *ROLES, "author", "genre", "files", "any"})

# what `any:` looks in (everything text-like; credits and genres included)
ANY_FIELDS = ("title", "series", "publisher", "isbn", "language", "summary", "kind", "url", "source", "author",
              "genre", "filename", "path", "format")


@dataclass
class Compiled:
    where: str
    params: list[Any] = field(default_factory=list)


def _like(value: str) -> str:
    return "%" + fold(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _numeric(expr: str, value: str) -> tuple[str, list[Any]]:
    def num(text: str) -> float:
        try:
            return float(text)
        except ValueError:
            raise QueryError(f"{text!r} is not a number") from None

    if ".." in value:
        low, high = value.split("..", 1)
        parts, params = [], []
        if low:
            parts.append(f"{expr} >= ?")
            params.append(num(low))
        if high:
            parts.append(f"{expr} <= ?")
            params.append(num(high))
        if not parts:
            raise QueryError("empty range")
        return "(" + " AND ".join(parts) + ")", params
    return f"{expr} = ?", [num(value)]


def _bool(expr: str, value: str) -> tuple[str, list[Any]]:
    truthy = {"true", "yes", "1", "y"}
    falsy = {"false", "no", "0", "n"}
    if value.lower() in truthy:
        return f"COALESCE({expr}, 0) = 1", []
    if value.lower() in falsy:
        return f"COALESCE({expr}, 0) = 0", []
    raise QueryError(f"{value!r} is not true/false")


def _text(expr: str, mode: str, value: str) -> tuple[str, list[Any]]:
    if mode == "regex":
        try:
            re.compile(value)
        except re.error as err:
            raise QueryError(f"bad regular expression {value!r}: {err}") from None
        return f"regexp(?, {expr})", [value]
    if mode == "exact":
        return f"fold({expr}) = ?", [fold(value)]
    return f"fold({expr}) LIKE ? ESCAPE '\\'", [_like(value)]


def _split(term: str) -> tuple[bool, str | None, str, str]:
    """-> (negated, field or None, mode, value)"""
    negated = term.startswith("^")
    if negated:
        term = term[1:]
    if term.startswith("::"):  # a bare regular expression: look everywhere
        return negated, "any", "regex", term[2:]
    if len(term) > 2 and term.startswith("/") and term.endswith("/"):
        return negated, "any", "regex", term[1:-1]
    field_name, sep, rest = term.partition(":")
    if not sep or field_name.lower() not in ALL_FIELDS:
        return negated, None, "substring", term  # free text (may legitimately contain ':')
    mode = "substring"
    if rest.startswith(":"):
        mode, rest = "regex", rest[1:]
    elif rest.startswith("="):
        mode, rest = "exact", rest[1:]
    if mode == "substring" and len(rest) > 2 and rest.startswith("/") and rest.endswith("/"):
        mode, rest = "regex", rest[1:-1]  # field:/regex/, the same as field::regex
    return negated, field_name.lower(), mode, rest


def compile_term(term: str, files_mode: bool) -> Compiled:
    negated, name, mode, value = _split(term)
    if not value and name is not None:
        raise QueryError(f"{name}: needs a value")

    def on_file(cond: str) -> str:
        """A condition on file columns: direct in file mode, EXISTS in album mode."""
        if files_mode:
            return cond
        return f"EXISTS (SELECT 1 FROM files f WHERE f.album_id = a.id AND {cond})"

    sql, params = "", []
    if name == "any":
        prefix = {"regex": ":", "exact": "="}.get(mode, "")
        parts = [compile_term(f"{field}:{prefix}{value}", files_mode) for field in ANY_FIELDS]
        sql = "(" + " OR ".join(p.where for p in parts) + ")"
        params = [x for p in parts for x in p.params]
    elif name is None:  # free text
        like = _like(value)
        credit = "EXISTS (SELECT 1 FROM album_credits c JOIN people p ON p.id = c.person_id " \
                 "WHERE c.album_id = a.id AND fold(p.name) LIKE ? ESCAPE '\\')"
        file_cond = on_file("fold(basename(f.path)) LIKE ? ESCAPE '\\'")
        sql = ("(fold(a.title) LIKE ? ESCAPE '\\' OR fold(s.name) LIKE ? ESCAPE '\\' OR "
               f"{credit} OR {file_cond})")
        params = [like, like, like, like]
    elif name in TEXT_FIELDS:
        sql, params = _text(TEXT_FIELDS[name], mode, value)
    elif name in FILE_TEXT_FIELDS:
        cond, params = _text(FILE_TEXT_FIELDS[name], mode, value)
        sql = on_file(cond)
    elif name in NUMERIC_FIELDS:
        sql, params = _numeric(NUMERIC_FIELDS[name], value)
    elif name in FILE_NUMERIC_FIELDS:
        cond, params = _numeric(FILE_NUMERIC_FIELDS[name], value)
        sql = on_file(cond)
    elif name == "files":
        sql, params = _numeric("(SELECT COUNT(*) FROM files f WHERE f.album_id = a.id)", value)
        if files_mode:
            raise QueryError("`files:` counts files per album: use it without --files")
    elif name in BOOL_FIELDS:
        sql, params = _bool(BOOL_FIELDS[name], value)
    elif name in FILE_BOOL_FIELDS:
        cond, params = _bool(FILE_BOOL_FIELDS[name], value)
        if name == "tagged" and not files_mode:
            raise QueryError("`tagged:` applies to files: use `bdnex ls --files tagged:false`")
        sql = on_file(cond)
    elif name == "genre":
        cond, params = _text("g.name", mode, value)
        sql = ("EXISTS (SELECT 1 FROM album_genres ag JOIN genres g ON g.id = ag.genre_id "
               f"WHERE ag.album_id = a.id AND {cond})")
    else:  # author / writer / penciller ...
        cond, params = _text("p.name", mode, value)
        role = ROLES.get(name)
        role_sql = " AND c.role = ?" if role else ""
        sql = ("EXISTS (SELECT 1 FROM album_credits c JOIN people p ON p.id = c.person_id "
               f"WHERE c.album_id = a.id{role_sql} AND {cond})")
        params = ([role] if role else []) + params

    if negated:
        sql = f"NOT COALESCE({sql}, 0)"
    return Compiled(sql, params)


def compile_query(terms: list[str], files_mode: bool = False) -> Compiled:
    parts = [compile_term(t, files_mode) for t in terms if t.strip()]
    if not parts:
        return Compiled("1 = 1", [])
    return Compiled(" AND ".join(p.where for p in parts), [x for p in parts for x in p.params])


ALBUM_COLUMNS = """a.id AS id, a.kind, s.name AS series, a.volume, a.alternate_number, a.title,
       pub.name AS publisher, a.isbn, a.year, a.month, a.day, a.pages, a.rating, a.language,
       a.url, a.source, a.source_id,
       COALESCE(json_extract(a.extra, '$.partial'), 0) AS partial,
       (SELECT group_concat(name, '; ') FROM (SELECT DISTINCT p.name FROM album_credits c
            JOIN people p ON p.id = c.person_id WHERE c.album_id = a.id ORDER BY p.name)) AS authors,
       (SELECT group_concat(name, ', ') FROM (SELECT g.name FROM album_genres ag
            JOIN genres g ON g.id = ag.genre_id WHERE ag.album_id = a.id ORDER BY g.name)) AS genres"""
FILE_COLUMNS = ("f.id AS file_id, f.path, f.format, f.size, f.md5, f.corrupted, f.page_count, f.has_metadata, "
                "f.embedded, f.review, f.spare, f.cover_score")
FROM_JOINS = """
LEFT JOIN series s ON s.id = a.series_id
LEFT JOIN publishers pub ON pub.id = a.publisher_id
"""
ALBUM_ORDER = "ORDER BY fold(COALESCE(s.name, a.title)), a.volume, fold(a.title)"
FILE_ORDER = "ORDER BY f.path"


def build_sql(terms: list[str], files_mode: bool) -> tuple[str, list[Any]]:
    compiled = compile_query(terms, files_mode)
    if files_mode:
        sql = (f"SELECT {FILE_COLUMNS}, {ALBUM_COLUMNS}, 1 AS files "
               f"FROM files f LEFT JOIN albums a ON a.id = f.album_id {FROM_JOINS} "
               f"WHERE {compiled.where} {FILE_ORDER}")
    else:
        sql = (f"SELECT {ALBUM_COLUMNS}, (SELECT COUNT(*) FROM files f WHERE f.album_id = a.id) AS files "
               f"FROM albums a {FROM_JOINS} WHERE {compiled.where} {ALBUM_ORDER}")
    return sql, compiled.params


def run_query(conn, terms: list[str], files_mode: bool = False) -> list[dict[str, Any]]:
    sql, params = build_sql(terms, files_mode)
    return [dict(row) for row in conn.execute(sql, params)]


__all__ = ["QueryError", "compile_query", "run_query", "fold", "register_functions", "ALL_FIELDS"]
