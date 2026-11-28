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
    attr:checked             files or albums you marked with `bdnex mark` (attr:checked=keep: with that value)
    nains , elfes            a lone comma is "or": albums matching either side (each side is ANDed)
    year-  title+            sort by a field, descending (-) or ascending (+); the order of the terms is the order of the keys
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any

ROLES = {"writer": "Writer", "penciller": "Penciller", "inker": "Inker", "colorist": "Colorist",
         "letterer": "Letterer", "coverartist": "CoverArtist"}


class QueryError(ValueError):
    """The query text is invalid."""


# letters that accent-stripping does not decompose: `Cœur` is `Coeur` in an address (without this the word lost its letters: `c ur`)
LIGATURES = str.maketrans({"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae", "ß": "ss", "ø": "o", "Ø": "o"})


@lru_cache(maxsize=1 << 17)  # a search folds every title, name and path of the library: the same ones again for the next term
def _fold(text: str) -> str:
    if text.isascii():  # nothing to strip: the common case, and the loop below is the cost of a search
        return text.casefold()
    text = unicodedata.normalize("NFKD", text.translate(LIGATURES))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def fold(value: Any) -> str:
    """Lowercase and strip accents, so ``astérix`` matches ``Asterix``."""
    if value is None:
        return ""
    return _fold(value if isinstance(value, str) else str(value))


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


MAX_REGEX = 300        # characters of a `::regex` pattern
MAX_SUBJECT = 2000     # characters of a value a pattern is tried on (a summary can be pages long)
NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)[+*{]")  # `(a+)+`, `(.*)*`, `(\w+\s?)+`: time that doubles with each character


def check_regex(pattern: str) -> None:
    """Refuse a pattern that can make a search run for ever (catastrophic backtracking: a quantifier inside a group that is itself repeated)
    or that is absurdly long. Raises :class:`QueryError`; a plain pattern is untouched."""
    if len(pattern) > MAX_REGEX:
        raise QueryError(f"a regular expression is at most {MAX_REGEX} characters ({len(pattern)})")
    if NESTED_QUANTIFIER.search(pattern):
        raise QueryError(f"regular expression {pattern!r}: a repeated group that repeats inside itself can take for ever on a long value; "
                         "write it without the nesting (`(a|b)+` is fine, `(a+)+` is not)")


def regexp(pattern: str, value: Any) -> bool:
    """Case- and accent-insensitive search. The pattern keeps its escapes (``\\D`` stays ``\\D``)."""
    if value is None:
        return False
    return re.search(strip_accents(pattern), strip_accents(str(value))[:MAX_SUBJECT], re.IGNORECASE) is not None


def register_functions(conn) -> None:
    conn.create_function("fold", 1, fold, deterministic=True)
    conn.create_function("regexp", 2, regexp, deterministic=True)
    conn.create_function("basename", 1, lambda p: (p or "").rsplit("/", 1)[-1], deterministic=True)


# field -> SQL expression (album mode and file mode share the same joins: a, s, pub, f)
TEXT_FIELDS = {"title": "a.title", "series": "s.name", "publisher": "pub.name", "isbn": "a.isbn",
               "language": "a.language", "summary": "a.summary", "kind": "a.kind",
               "url": "a.url", "source": "a.source", "subseries": "a.subseries",
               "collection": "a.collection", "cycle": "a.cycle", "category": "a.category", "relation": "a.relation"}
FILE_TEXT_FIELDS = {"path": "f.path", "filename": "basename(f.path)", "format": "f.format", "md5": "f.md5",
                    "embedded": "COALESCE(f.embedded, '')"}
NUMERIC_FIELDS = {"id": "a.id", "year": "a.year", "volume": "a.volume", "pages": "a.pages", "rating": "a.rating"}
FILE_NUMERIC_FIELDS = {"fileid": "f.id", "size": "f.size", "filepages": "f.page_count", "cover": "f.cover_score", "dpi": "f.dpi"}
BOOL_FIELDS = {"partial": "COALESCE(json_extract(a.extra, '$.partial'), 0)",
               "complete": "(1 - COALESCE(json_extract(a.extra, '$.partial'), 0))"}  # its page was read
FILE_BOOL_FIELDS = {"corrupted": "f.corrupted", "tagged": "(f.album_id IS NOT NULL)",
                    "metadata": "f.has_metadata", "spare": "f.spare", "review": "(f.review IS NOT NULL)",
                    "staged": "EXISTS (SELECT 1 FROM staged_files sf WHERE sf.file_id = f.id)"}

ALL_FIELDS = sorted({*TEXT_FIELDS, *FILE_TEXT_FIELDS, *NUMERIC_FIELDS, *FILE_NUMERIC_FIELDS, *BOOL_FIELDS,
                     *FILE_BOOL_FIELDS, *ROLES, "author", "genre", "files", "any", "attr"})

# what `any:` looks in (everything text-like; credits and genres included)
ANY_FIELDS = ("title", "series", "publisher", "isbn", "language", "summary", "kind", "url", "source", "author",
              "genre", "filename", "path", "format", "subseries", "collection", "cycle", "category")


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

    if "," in value:  # fileid:3,7,12: a list of numbers (the screen sends the files of its view this way)
        return f"{expr} IN ({','.join(str(int(num(part))) for part in value.split(',') if part)})", []
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
        check_regex(value)
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
    elif name == "attr":  # your own marks (`bdnex mark`), on the file or on the album
        mark, equals, wanted = value.partition("=")
        mark = mark.strip().lower()
        if not mark:
            raise QueryError("attr: needs a mark name, e.g. attr:checked or attr:checked=keep")
        test, extra = ("", []) if not equals else (" AND fold(x.value) = ?", [fold(wanted)])
        on_f = f"EXISTS (SELECT 1 FROM file_attrs x WHERE x.file_id = f.id AND x.key = ?{test})"
        on_a = f"EXISTS (SELECT 1 FROM album_attrs x WHERE x.album_id = a.id AND x.key = ?{test})"
        if files_mode:
            sql, params = f"({on_f} OR {on_a})", [mark, *extra, mark, *extra]
        else:
            in_files = f"EXISTS (SELECT 1 FROM files f WHERE f.album_id = a.id AND {on_f})"
            sql, params = f"({on_a} OR {in_files})", [mark, *extra, mark, *extra]
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


SORT_TERM = re.compile(r"^([a-z]+)([+-])$")


def sort_fields() -> dict[str, tuple[str, bool]]:
    """Fields a query can sort on: name -> (SQL expression, is text)."""
    fields = {name: (expr, True) for name, expr in {**TEXT_FIELDS, **FILE_TEXT_FIELDS}.items()}
    fields.update({name: (expr, False) for name, expr in {**NUMERIC_FIELDS, **FILE_NUMERIC_FIELDS}.items()})
    return fields


def split_query(terms: list[str]) -> tuple[list[list[str]], list[str]]:
    """``(alternatives, sort terms)``: the terms cut at each lone ``,`` (OR) and the ``field+`` / ``field-`` ones set aside."""
    groups: list[list[str]] = [[]]
    sort: list[str] = []
    for term in terms:
        if term.strip() == ",":
            groups.append([])
        elif SORT_TERM.match(term) and SORT_TERM.match(term).group(1) in sort_fields():
            sort.append(term)
        elif term.strip():
            groups[-1].append(term)
    return [g for g in groups if g] or [[]], sort


def order_by(sort: list[str]) -> str:
    fields = sort_fields()
    parts = []
    for term in sort:
        name, sign = SORT_TERM.match(term).groups()
        expr, text = fields[name]
        parts.append(f"{'fold(' + expr + ')' if text else expr} {'DESC' if sign == '-' else 'ASC'}")
    return ", ".join(parts)


def compile_query(terms: list[str], files_mode: bool = False) -> Compiled:
    groups, _ = split_query(terms)
    clauses, params = [], []
    for group in groups:
        parts = [compile_term(t, files_mode) for t in group]
        clauses.append("(" + (" AND ".join(p.where for p in parts) if parts else "1 = 1") + ")")
        params += [x for p in parts for x in p.params]
    return Compiled(clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")", params)


ALBUM_COLUMNS = """a.id AS id, a.kind, s.name AS series, a.volume, a.alternate_number, a.title,
       pub.name AS publisher, a.isbn, a.year, a.month, a.day, a.pages, a.rating, a.language,
       a.url, a.source, a.source_id,
       COALESCE(json_extract(a.extra, '$.partial'), 0) AS partial,
       (SELECT group_concat(name, '; ') FROM (SELECT DISTINCT p.name FROM album_credits c
            JOIN people p ON p.id = c.person_id WHERE c.album_id = a.id ORDER BY p.name)) AS authors,
       (SELECT group_concat(name, ', ') FROM (SELECT g.name FROM album_genres ag
            JOIN genres g ON g.id = ag.genre_id WHERE ag.album_id = a.id ORDER BY g.name)) AS genres"""
FILE_COLUMNS = ("f.id AS file_id, f.path, f.format, f.size, f.md5, f.corrupted, f.page_count, f.has_metadata, "
                "f.embedded, f.review, f.spare, f.cover_score, f.dpi")
FROM_JOINS = """
LEFT JOIN series s ON s.id = a.series_id
LEFT JOIN publishers pub ON pub.id = a.publisher_id
"""
ALBUM_ORDER = "ORDER BY fold(COALESCE(s.name, a.title)), a.volume, fold(a.title)"
FILE_ORDER = "ORDER BY f.path"


# the same columns without the two correlated subqueries that build `authors` and `genres` (most of the cost of a listing
# of files): for callers that read the file, its album id, series, volume, title or url, never the credits or `partial`
LIGHT_ALBUM_COLUMNS = (ALBUM_COLUMNS.split("COALESCE(json_extract(a.extra, '$.partial'), 0) AS partial,")[0]
                       + "NULL AS partial, NULL AS authors, NULL AS genres")  # `partial` parses the album's whole JSON, per row


def build_sql(terms: list[str], files_mode: bool, light: bool = False, file_ids: list[int] | None = None) -> tuple[str, list[Any]]:
    """``file_ids`` (files only): only these files, whatever else the terms say (the window of a page, built in full)."""
    compiled = compile_query(terms, files_mode)
    _, sort = split_query(terms)
    if files_mode:
        order = f"ORDER BY {order_by(sort)}" if sort else FILE_ORDER
        where, params = compiled.where, list(compiled.params)
        if file_ids:
            where = f"({where}) AND f.id IN ({','.join('?' * len(file_ids))})"
            params += file_ids
        sql = (f"SELECT {FILE_COLUMNS}, {LIGHT_ALBUM_COLUMNS if light else ALBUM_COLUMNS}, 1 AS files "
               f"FROM files f LEFT JOIN albums a ON a.id = f.album_id {FROM_JOINS} WHERE {where} {order}")
        return sql, params
    else:
        order = f"ORDER BY {order_by(sort)}" if sort else ALBUM_ORDER
        sql = (f"SELECT {ALBUM_COLUMNS}, (SELECT COUNT(*) FROM files f WHERE f.album_id = a.id) AS files "
               f"FROM albums a {FROM_JOINS} WHERE {compiled.where} {order}")
    return sql, compiled.params


def run_query(conn, terms: list[str], files_mode: bool = False, light: bool = False) -> list[dict[str, Any]]:
    sql, params = build_sql(terms, files_mode, light)
    return [dict(row) for row in conn.execute(sql, params)]


__all__ = ["QueryError", "compile_query", "run_query", "fold", "register_functions", "ALL_FIELDS", "split_query"]
