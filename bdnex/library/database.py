"""SQLite-backed library: files, albums, series, people, genres and a source cache."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Iterable, Optional

from bdnex.library.hashing import file_md5
from bdnex.library.query import fold, register_functions, run_query
from bdnex.library.models import AlbumInfo, SeriesInfo
from bdnex.library.scan import inspect_file, iter_comic_files
from bdnex.library.schema import MIGRATIONS

SCHEMA_VERSION = len(MIGRATIONS)

logger = logging.getLogger(__name__)


class SchemaTooNewError(RuntimeError):
    """The database was created by a newer bdnex."""


class Library:
    """Open (and create / migrate if needed) a bdnex library database.

    Use ``":memory:"`` for a throwaway database.
    """

    def __init__(self, path=":memory:"):
        if path != ":memory:":
            path = os.path.expanduser(str(path))
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        register_functions(self.conn)
        self._migrate()

    # ------------------------------------------------------------------ setup
    def _migrate(self):
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise SchemaTooNewError(
                f"{self.path} has schema v{current}, this bdnex only knows v{SCHEMA_VERSION}")
        for version in range(current, SCHEMA_VERSION):
            logger.info(f"Migrating library schema to v{version + 1}")
            with self.conn:
                self.conn.executescript("BEGIN;" + MIGRATIONS[version])
                self.conn.execute(f"PRAGMA user_version = {version + 1}")

    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --------------------------------------------------------------- helpers
    def _get_or_create(self, table: str, name: str) -> int:
        row = self.conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        return self.conn.execute(f"INSERT INTO {table}(name) VALUES (?)", (name,)).lastrowid

    # ---------------------------------------------------------------- series
    def add_series(self, info: SeriesInfo) -> int:
        with self.conn:
            self.conn.execute(
                """INSERT INTO series(source, source_id, name, summary, url)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source, source_id) DO UPDATE SET
                     name = excluded.name, summary = excluded.summary, url = excluded.url""",
                (info.source, info.source_id, info.name, info.summary, info.url))
        return self.conn.execute("SELECT id FROM series WHERE source = ? AND source_id = ?",
                                 (info.source, info.source_id)).fetchone()["id"]

    # ---------------------------------------------------------------- albums
    def add_album(self, info: AlbumInfo) -> int:
        """Insert or update an album (identified by source + source_id)."""
        with self.conn:
            series_id = self.add_series(info.series) if info.series else None
            publisher_id = self._get_or_create("publishers", info.publisher) if info.publisher else None
            self.conn.execute(
                """INSERT INTO albums(kind, series_id, title, volume, alternate_number,
                       publisher_id, isbn, year, month, day, pages, language, format,
                       summary, rating, cover_url, url, source, source_id, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, source_id) DO UPDATE SET
                       kind = excluded.kind, series_id = excluded.series_id,
                       title = excluded.title, volume = excluded.volume,
                       alternate_number = excluded.alternate_number,
                       publisher_id = excluded.publisher_id, isbn = excluded.isbn,
                       year = excluded.year, month = excluded.month, day = excluded.day,
                       pages = excluded.pages, language = excluded.language,
                       format = excluded.format, summary = excluded.summary,
                       rating = excluded.rating, cover_url = excluded.cover_url,
                       url = excluded.url, extra = excluded.extra""",
                (info.kind, series_id, info.title, info.volume, info.alternate_number,
                 publisher_id, info.isbn, info.year, info.month, info.day, info.pages,
                 info.language, info.format, info.summary, info.rating, info.cover_url,
                 info.url, info.source, info.source_id,
                 json.dumps(info.extra, ensure_ascii=False, default=str, sort_keys=True)))
            album_id = self.conn.execute(
                "SELECT id FROM albums WHERE source = ? AND source_id = ?",
                (info.source, info.source_id)).fetchone()["id"]

            # credits and genres are replaced wholesale: the source is authoritative
            self.conn.execute("DELETE FROM album_credits WHERE album_id = ?", (album_id,))
            for role, names in info.credits.items():
                for name in names:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO album_credits(album_id, person_id, role) VALUES (?, ?, ?)",
                        (album_id, self._get_or_create("people", name), role))
            self.conn.execute("DELETE FROM album_genres WHERE album_id = ?", (album_id,))
            for genre in info.genres:
                self.conn.execute(
                    "INSERT OR IGNORE INTO album_genres(album_id, genre_id) VALUES (?, ?)",
                    (album_id, self._get_or_create("genres", genre)))
        return album_id

    EDITABLE_ALBUM_COLUMNS = frozenset({"title", "year", "month", "day", "volume", "language", "isbn",
                                        "pages", "summary"})

    def set_album_fields(self, album_id: int, **columns: Any) -> None:
        """Update simple album columns (and ``publisher`` by name), e.g. a value typed at a prompt."""
        publisher = columns.pop("publisher", None)
        unknown = set(columns) - self.EDITABLE_ALBUM_COLUMNS
        if unknown:
            raise ValueError(f"can not edit: {', '.join(sorted(unknown))}")
        with self.conn:
            if columns:
                assignments = ", ".join(f"{c} = ?" for c in columns)
                self.conn.execute(f"UPDATE albums SET {assignments} WHERE id = ?", [*columns.values(), album_id])
            if publisher:
                self.conn.execute("UPDATE albums SET publisher_id = ? WHERE id = ?",
                                  (self._get_or_create("publishers", publisher), album_id))

    def add_credit(self, album_id: int, role: str, name: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO album_credits(album_id, person_id, role) VALUES (?, ?, ?)",
                (album_id, self._get_or_create("people", name), role))

    def prune_orphan_series(self) -> int:
        """Delete series no album refers to (e.g. slug-based placeholders replaced by real ones)."""
        with self.conn:
            return self.conn.execute(
                "DELETE FROM series WHERE id NOT IN (SELECT series_id FROM albums WHERE series_id IS NOT NULL)"
            ).rowcount

    def files_for_album(self, album_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM files WHERE album_id = ? ORDER BY path", (album_id,))]

    def files_matching_album(self, source: str, source_id: str, keys: dict[str, Any],
                             exclude_path: str) -> list[dict[str, Any]]:
        """Files linked to the same album (``source``/``source_id``) or to an album with the same
        ``keys`` values (``series``, ``volume``, ``title``; accents and case ignored)."""
        clauses, params = [], []
        if "series" in keys:
            clauses.append("fold(s.name) = ?")
            params.append(fold(keys["series"]))
        if "volume" in keys:
            clauses.append("a.volume = ?")
            params.append(keys["volume"])
        if "title" in keys:
            clauses.append("fold(a.title) = ?")
            params.append(fold(keys["title"]))
        same_keys = f"({' AND '.join(clauses)})" if clauses else "0"
        sql = ("SELECT f.* FROM files f JOIN albums a ON a.id = f.album_id "
               "LEFT JOIN series s ON s.id = a.series_id "
               f"WHERE f.path != ? AND ((a.source = ? AND a.source_id = ?) OR {same_keys}) ORDER BY f.path")
        return [dict(r) for r in self.conn.execute(sql, [os.path.abspath(exclude_path), source, source_id, *params])]

    def files_with_md5(self, md5: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM files WHERE md5 = ? ORDER BY path", (md5,))]

    # ------------------------------------------------------- merging a fresh parse
    SCALARS = ("title", "volume", "alternate_number", "publisher", "isbn", "year", "month", "day", "pages",
               "language", "format", "summary", "rating", "cover_url", "url")

    def _merged_info(self, old: dict[str, Any], info: AlbumInfo) -> AlbumInfo:
        """``info`` completed with what the library already holds: a fresh parse overrides the values it
        has, and never erases the ones it lacks (e.g. a credit typed at a prompt)."""
        series = info.series
        if series is None and old["kind"] == "series":
            row = self.conn.execute(
                "SELECT s.* FROM series s JOIN albums a ON a.series_id = s.id WHERE a.id = ?", (old["id"],)).fetchone()
            series = SeriesInfo(row["source"], row["source_id"], row["name"], row["summary"], row["url"]) if row else None
        credits = {**old["credits"], **{role: names for role, names in info.credits.items() if names}}
        extra = {**old["extra"], **info.extra}
        if not info.extra.get("partial"):
            extra.pop("partial", None)  # a full parse replaces a sitemap-only record
        values = {key: getattr(info, key) if getattr(info, key) not in (None, "") else old.get(key)
                  for key in self.SCALARS}
        return AlbumInfo(source=info.source, source_id=info.source_id, series=series, credits=credits,
                         genres=info.genres or old["genres"], extra=extra,
                         publisher=values.pop("publisher"), **values)

    def preview_merge(self, info: AlbumInfo) -> list[str]:
        """What :meth:`merge_album` would change, one short line each (empty when nothing)."""
        album_id = self.find_album(info.source, info.source_id)
        if album_id is None:
            return ["new album"]
        old = self.get_album(album_id)
        new = self._merged_info(old, info)
        changes = []
        for key in self.SCALARS + ("publisher",):
            if getattr(new, key) != old.get(key):
                changes.append(f"{key}: {old.get(key)!r} -> {getattr(new, key)!r}")
        if (new.series.name if new.series else None) != old["series"]:
            changes.append(f"series: {old['series']!r} -> {new.series.name if new.series else None!r}")
        for role in sorted(set(new.credits) | set(old["credits"])):
            if new.credits.get(role, []) != old["credits"].get(role, []):
                changes.append(f"{role}: {', '.join(old['credits'].get(role, [])) or '-'} -> "
                               f"{', '.join(new.credits.get(role, [])) or '-'}")
        if sorted(new.genres) != sorted(old["genres"]):
            changes.append(f"genres: {', '.join(old['genres']) or '-'} -> {', '.join(new.genres) or '-'}")
        added = sorted(set(new.extra) - set(old["extra"]))
        changed = sorted(k for k in set(new.extra) & set(old["extra"]) if new.extra[k] != old["extra"][k])
        removed = sorted(set(old["extra"]) - set(new.extra))
        for label, keys in (("new details", added), ("updated details", changed), ("dropped details", removed)):
            if keys:
                changes.append(f"{label}: {', '.join(keys)}")
        return changes

    def merge_album(self, info: AlbumInfo) -> int:
        """Store a fresh parse of an existing album without erasing anything it does not mention."""
        album_id = self.find_album(info.source, info.source_id)
        if album_id is None:
            return self.add_album(info)
        return self.add_album(self._merged_info(self.get_album(album_id), info))

    def get_album(self, album_id: int) -> Optional[dict[str, Any]]:
        """Return an album as a flat dict with series, publisher, credits and genres."""
        row = self.conn.execute(
            """SELECT a.*, s.name AS series, p.name AS publisher
               FROM albums a
               LEFT JOIN series s ON s.id = a.series_id
               LEFT JOIN publishers p ON p.id = a.publisher_id
               WHERE a.id = ?""", (album_id,)).fetchone()
        if row is None:
            return None
        album = dict(row)
        album["extra"] = json.loads(album["extra"])
        album.pop("series_id"), album.pop("publisher_id")
        credits: dict[str, list[str]] = {}
        for r in self.conn.execute(
                """SELECT c.role, pe.name FROM album_credits c
                   JOIN people pe ON pe.id = c.person_id
                   WHERE c.album_id = ? ORDER BY c.role, pe.name""", (album_id,)):
            credits.setdefault(r["role"], []).append(r["name"])
        album["credits"] = credits
        album["genres"] = [r["name"] for r in self.conn.execute(
            """SELECT g.name FROM album_genres ag JOIN genres g ON g.id = ag.genre_id
               WHERE ag.album_id = ? ORDER BY g.name""", (album_id,))]
        return album

    def find_album(self, source: str, source_id: str) -> Optional[int]:
        row = self.conn.execute("SELECT id FROM albums WHERE source = ? AND source_id = ?",
                                (source, source_id)).fetchone()
        return row["id"] if row else None

    # ----------------------------------------------------------------- files
    def add_file(self, path, album_id: Optional[int] = None) -> int:
        """Inspect, hash and register a file (update it if the path is known)."""
        info = inspect_file(path)
        md5 = file_md5(info.path)
        with self.conn:
            self.conn.execute(
                """INSERT INTO files(path, album_id, md5, size, format, page_count,
                                     corrupted, has_metadata, mtime)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       album_id = COALESCE(excluded.album_id, files.album_id),
                       md5 = excluded.md5, size = excluded.size, format = excluded.format,
                       page_count = excluded.page_count, corrupted = excluded.corrupted,
                       has_metadata = excluded.has_metadata, mtime = excluded.mtime,
                       checked = CURRENT_TIMESTAMP""",
                (info.path, album_id, md5, info.size, info.format, info.page_count,
                 int(info.corrupted), int(info.has_metadata), info.mtime))
        return self.conn.execute("SELECT id FROM files WHERE path = ?",
                                 (info.path,)).fetchone()["id"]

    def get_file(self, path) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM files WHERE path = ?",
                                (os.path.abspath(path),)).fetchone()
        return dict(row) if row else None

    def refresh_file(self, path) -> int:
        """Recompute size/md5/... after a file was modified (e.g. tags written)."""
        return self.add_file(path)

    def move_file(self, old_path, new_path) -> None:
        """Record that a file moved/was renamed, and refresh its checksum."""
        old_path, new_path = os.path.abspath(old_path), os.path.abspath(new_path)
        with self.conn:
            cur = self.conn.execute("UPDATE files SET path = ? WHERE path = ?",
                                    (new_path, old_path))
        if cur.rowcount == 0:
            raise KeyError(f"{old_path} is not in the library")
        self.refresh_file(new_path)

    def set_file_album(self, path, album_id: Optional[int]) -> None:
        with self.conn:
            self.conn.execute("UPDATE files SET album_id = ? WHERE path = ?",
                              (album_id, os.path.abspath(path)))

    def remove_file(self, path) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE path = ?", (os.path.abspath(path),))

    def scan(self, root) -> dict[str, int]:
        """Register every comic file under ``root``; return counters."""
        counts = {"added": 0, "updated": 0, "corrupted": 0}
        for path in iter_comic_files(root):
            known = self.get_file(path) is not None
            self.add_file(path)
            counts["updated" if known else "added"] += 1
            if self.get_file(path)["corrupted"]:
                counts["corrupted"] += 1
        return counts

    def duplicate_files(self) -> list[list[dict[str, Any]]]:
        """Groups of files sharing the same checksum."""
        groups = []
        for row in self.conn.execute(
                "SELECT md5 FROM files GROUP BY md5 HAVING COUNT(*) > 1 ORDER BY md5"):
            groups.append([dict(r) for r in self.conn.execute(
                "SELECT * FROM files WHERE md5 = ? ORDER BY path", (row["md5"],))])
        return groups

    # ---------------------------------------------------------------- query
    def query(self, terms: list[str], files: bool = False) -> list[dict[str, Any]]:
        """Albums (or files with ``files=True``) matching the query terms. See ``library.query``."""
        return run_query(self.conn, terms, files_mode=files)

    # ----------------------------------------------------------- source cache
    def cache_put(self, source: str, kind: str, url: str, content: bytes | str) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8")
        with self.conn:
            self.conn.execute(
                """INSERT INTO source_cache(source, kind, url, content) VALUES (?, ?, ?, ?)
                   ON CONFLICT(source, kind, url) DO UPDATE SET
                       content = excluded.content, fetched = CURRENT_TIMESTAMP""",
                (source, kind, url, content))

    def cache_get(self, source: str, kind: str, url: str) -> Optional[bytes]:
        row = self.conn.execute(
            "SELECT content FROM source_cache WHERE source = ? AND kind = ? AND url = ?",
            (source, kind, url)).fetchone()
        return row["content"] if row else None

    def cache_clear(self, source: str, kind: Optional[str] = None) -> int:
        """Drop cached entries (the ``--recache`` option). Returns rows removed."""
        sql, params = "DELETE FROM source_cache WHERE source = ?", [source]
        if kind:
            sql, params = sql + " AND kind = ?", params + [kind]
        with self.conn:
            return self.conn.execute(sql, params).rowcount

    # ------------------------------------------------------------ import log
    def log_import(self, path, outcome: str, score: Optional[float] = None,
                   album_id: Optional[int] = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO import_log(path, outcome, score, album_id) VALUES (?, ?, ?, ?)",
                (os.path.abspath(path), outcome, score, album_id))

    def last_import(self, path) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM import_log WHERE path = ? ORDER BY id DESC LIMIT 1",
            (os.path.abspath(path),)).fetchone()
        return dict(row) if row else None

    # ----------------------------------------------------------------- stats
    def stats(self) -> dict[str, int]:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "files": q("SELECT COUNT(*) FROM files"),
            "corrupted": q("SELECT COUNT(*) FROM files WHERE corrupted = 1"),
            "untagged": q("SELECT COUNT(*) FROM files WHERE album_id IS NULL"),
            "albums": q("SELECT COUNT(*) FROM albums"),
            "series": q("SELECT COUNT(*) FROM series"),
            "oneshots": q("SELECT COUNT(*) FROM albums WHERE kind = 'oneshot'"),
            "people": q("SELECT COUNT(*) FROM people"),
        }
