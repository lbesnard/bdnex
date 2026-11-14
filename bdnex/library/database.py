"""SQLite-backed library: files, albums, series, people, genres and a source cache."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sqlite3
import sys
from typing import Any, Iterable, Optional

from bdnex.library.hashing import file_md5
from bdnex.library.query import FROM_JOINS, compile_query, fold, register_functions, run_query
from bdnex.library.models import AlbumInfo, SeriesInfo
from bdnex.library.scan import inspect_file, iter_comic_files
from bdnex.library.schema import MIGRATIONS

SCHEMA_VERSION = len(MIGRATIONS)

logger = logging.getLogger(__name__)


class SchemaTooNewError(RuntimeError):
    """The database was created by a newer bdnex."""


class LibraryRootError(RuntimeError):
    """The database holds paths relative to the library folder, but no folder is configured."""


def analyse_file(path):
    """``(FileInfo, md5)`` of a comic file: the slow part of registering it (the whole file is read twice). It
    touches no database, so it can run in another process."""
    info = inspect_file(path)
    return info, file_md5(info.path)


def analyse_many(paths: list, workers: int = 1):
    """``(path, (FileInfo, md5) or an Exception)`` for each path, in order. With ``workers`` > 1 the files are
    read in several processes at once (a big collection on a slow disk is mostly waiting)."""
    from bdnex.parallel import map_ordered

    yield from map_ordered(analyse_file, paths, workers)


class Library:
    """Open (and create / migrate if needed) a bdnex library database.

    Use ``":memory:"`` for a throwaway database.

    ``root`` is the library folder (``collection_path`` in the configuration). The paths of files under it
    are stored relative to it, so moving the collection only means changing ``collection_path``; other
    files keep their absolute path. The API always takes and returns absolute paths.
    """

    @classmethod
    def for_settings(cls, settings, readonly=False, sync_sources=True) -> "Library":
        """The library of a loaded configuration (its database and its ``collection_path``)."""
        from bdnex.config import PLACEHOLDER_COLLECTION

        root = settings.collection_path if settings.collection_path not in ("", PLACEHOLDER_COLLECTION) else None
        library = cls(settings.library_path, root=root, readonly=readonly)
        if settings.import_.sources and not readonly and sync_sources:  # files under a source are stored as @alias/…
            for problem in library.sync_sources([(source.alias, source.folder) for source in settings.import_.sources]):
                print(f"warning: {problem}", file=sys.stderr)
        return library

    def __init__(self, path=":memory:", root=None, readonly=False):
        """``readonly``: open the file read-only, changing nothing (no migration, no path rewriting); for servers."""
        if path != ":memory:":
            path = os.path.expanduser(str(path))
            if not readonly:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        if readonly:
            # a read-only connection may be closed by another thread than the one that opened it (a web request
            # is set up and torn down in different worker threads)
            self.conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, check_same_thread=False)
        else:
            self.conn = sqlite3.connect(path, timeout=30)  # several processes may write (see `Organizer.run(workers=)`)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        register_functions(self.conn)
        self.root = os.path.abspath(os.path.expanduser(str(root))).rstrip("/") if root else None
        if readonly:
            self.conn.execute("PRAGMA query_only = ON")
            found = self.schema_version
            if found != SCHEMA_VERSION:
                self.conn.close()
                raise SchemaTooNewError(
                    f"{self.path} has schema v{found}, this bdnex needs v{SCHEMA_VERSION}: "
                    "run any other bdnex command once (for example `bdnex ls -c`) to update it")
            self._load_roots()
            return
        self._migrate()
        self._load_roots()
        self._adopt_root()

    # ------------------------------------------------------------------ setup
    def _migrate(self):
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise SchemaTooNewError(
                f"{self.path} has schema v{current}, this bdnex only knows v{SCHEMA_VERSION}")
        if current and current < SCHEMA_VERSION:
            self._back_up(current)
        for version in range(current, SCHEMA_VERSION):
            logger.info(f"Migrating library schema to v{version + 1}")
            with self.conn:
                self.conn.executescript("BEGIN;" + MIGRATIONS[version])
                self.conn.execute(f"PRAGMA user_version = {version + 1}")

    def _back_up(self, version: int) -> None:
        """Before an upgrade changes the file for good (an older bdnex can not open it any more), keep a copy:
        ``<library>.bak-v<version>``. One copy per version; an existing one is never overwritten."""
        if self.path == ":memory:":
            return
        target = f"{self.path}.bak-v{version}"
        if os.path.exists(target):
            return
        try:
            copy = sqlite3.connect(target)
            with copy:
                self.conn.backup(copy)
            copy.close()
        except (sqlite3.Error, OSError) as err:  # a copy that fails must not make the library unusable
            logger.warning(f"could not back up {self.path} before upgrading it: {err}")
            return
        print(f"bdnex: your library (schema v{version}) is being upgraded to v{SCHEMA_VERSION}; the old one is kept "
              f"as {target}", file=sys.stderr)

    # ------------------------------------------------------------------ paths
    def _load_roots(self) -> None:
        self.roots: dict[str, str] = {row["alias"]: row["path"] for row in self.conn.execute("SELECT alias, path FROM roots")}

    def _store(self, path) -> str:
        """The form of ``path`` kept in the database: relative to the collection when under it, ``@alias/…`` when
        under a registered source, absolute otherwise."""
        absolute = os.path.abspath(os.path.expanduser(str(path)))
        if self.root and absolute.startswith(self.root + os.sep):
            relative = absolute[len(self.root) + 1:]
            return "./" + relative if relative.startswith("@") else relative  # '@' is the mark of a source
        for alias, folder in sorted(self.roots.items(), key=lambda item: -len(item[1])):
            if absolute.startswith(folder + os.sep):
                return f"@{alias}/{absolute[len(folder) + 1:]}"
        return absolute

    def _resolve(self, stored: str) -> str:
        """The absolute path of a stored one."""
        if os.path.isabs(stored):
            return stored
        if stored.startswith("@"):
            alias, _, relative = stored[1:].partition("/")
            if alias not in self.roots:
                raise LibraryRootError(f"a file is stored under the source {alias!r}, which is not registered "
                                       "(`import.sources` in the configuration)")
            return os.path.join(self.roots[alias], relative)
        if stored.startswith("./"):
            stored = stored[2:]
        if not self.root:
            raise LibraryRootError(
                "this library stores paths relative to your comics folder, but `collection_path` is not set "
                "in the configuration (`bdnex config --edit`)")
        return os.path.join(self.root, stored)

    # ------------------------------------------------------------- sources (roots)
    def add_root(self, alias: str, path) -> bool:
        """Register a source folder under ``alias``; paths already stored under it become ``@alias/…``.
        Returns ``False`` when the alias is registered for another folder (nothing changes: `bdnex relocate`)."""
        folder = os.path.abspath(os.path.expanduser(str(path))).rstrip("/")
        known = self.roots.get(alias)
        if known is not None:
            return known == folder
        if folder in self.roots.values():  # registered under another name (the folder was renamed): the same source
            return True
        with self.conn:
            self.conn.execute("INSERT INTO roots(alias, path) VALUES (?, ?)", (alias, folder))
            prefix = folder + os.sep
            for table, column in (("files", "path"), ("import_log", "path"), ("trash", "path"),
                                  ("staged_files", "origin"), ("origins", "origin")):
                for row in self.conn.execute(f"SELECT rowid AS rid, {column} AS value FROM {table} "
                                             f"WHERE substr({column}, 1, ?) = ?", (len(prefix), prefix)).fetchall():
                    try:
                        self.conn.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                                          (f"@{alias}/{row['value'][len(prefix):]}", row["rid"]))
                    except sqlite3.IntegrityError:
                        logger.warning(f"{row['value']} is registered twice; left as it was")
        self._load_roots()
        return True

    def sync_sources(self, sources: list[tuple[str, str]]) -> list[str]:
        """Register the configured sources ``[(alias, path)]``. Returns a warning per source whose alias is already
        registered for another folder (it was moved: `bdnex relocate OLD NEW`)."""
        problems = []
        for alias, path in sources:
            if not self.add_root(alias, path):
                problems.append(f"the source {alias!r} is registered at {self.roots[alias]} but your configuration says "
                                f"{os.path.abspath(os.path.expanduser(path))}: if the folder moved, run "
                                f"`bdnex relocate {self.roots[alias]} {path}`")
        return problems

    def root_of(self, path) -> Optional[str]:
        """The alias of the registered source a path is under (``None``: not under one)."""
        absolute = os.path.abspath(os.path.expanduser(str(path)))
        for alias, folder in sorted(self.roots.items(), key=lambda item: -len(item[1])):
            if absolute == folder or absolute.startswith(folder + os.sep):
                return alias
        return None

    # ------------------------------------------------------------ origins (copies)
    def record_origin(self, path, origin, size: int, mtime: float) -> None:
        """``path`` is a copy of ``origin`` (a file as it was then: size and date)."""
        row = self.get_file(path)
        if row is None:
            raise KeyError(f"{path} is not in the library")
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO origins(file_id, origin, size, mtime) VALUES (?, ?, ?, ?)",
                              (row["id"], self._store(origin), size, mtime))

    def get_origin(self, origin) -> Optional[dict[str, Any]]:
        """What was copied from this source path: ``{"path": where the copy is now, "size", "mtime", "copied"}``."""
        row = self.conn.execute(
            "SELECT f.path AS path, o.size, o.mtime, o.copied FROM origins o JOIN files f ON f.id = o.file_id "
            "WHERE o.origin = ?", (self._store(origin),)).fetchone()
        if row is None:
            return None
        found = dict(row)
        found["path"] = self._resolve(found["path"])
        return found

    def _file_row(self, row) -> dict[str, Any]:
        record = dict(row)
        if "path" in record and record["path"] is not None:
            record["path"] = self._resolve(record["path"])
        if isinstance(record.get("embedded"), str):
            record["embedded"] = json.loads(record["embedded"])
        return record

    def _adopt_root(self) -> None:
        """Store the paths under the root relative to it; refuse relative ones without a root."""
        if not self.root:
            if self.conn.execute("SELECT 1 FROM files WHERE path NOT LIKE '/%' AND path NOT LIKE '@%' LIMIT 1").fetchone():
                self.conn.close()
                raise LibraryRootError(
                    "this library stores paths relative to your comics folder, but `collection_path` is not set "
                    "in the configuration (`bdnex config --edit`)")
            return
        prefix = self.root + os.sep
        converted = 0
        with self.conn:
            for table in ("files", "import_log"):
                rows = self.conn.execute(
                    f"SELECT rowid AS rid, path FROM {table} WHERE substr(path, 1, ?) = ?", (len(prefix), prefix)).fetchall()
                for row in rows:
                    try:
                        self.conn.execute(f"UPDATE {table} SET path = ? WHERE rowid = ?",
                                          (row["path"][len(prefix):], row["rid"]))
                        converted += 1
                    except sqlite3.IntegrityError:  # the relative form is registered already: leave it
                        logger.warning(f"{row['path']} is registered twice; left as it was")
        if converted:
            logger.info(f"Stored {converted} path(s) relative to {self.root}")

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
        """Insert or update a series. What it already holds in ``extra`` and the new values are merged, and
        a summary or address is not erased by a record that lacks one."""
        with self.conn:
            row = self.conn.execute("SELECT extra, summary, url FROM series WHERE source = ? AND source_id = ?",
                                    (info.source, info.source_id)).fetchone()
            extra = {**json.loads(row["extra"]), **info.extra} if row else dict(info.extra)
            self.conn.execute(
                """INSERT INTO series(source, source_id, name, summary, url, extra)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, source_id) DO UPDATE SET
                     name = excluded.name, extra = excluded.extra,
                     summary = COALESCE(excluded.summary, series.summary),
                     url = COALESCE(excluded.url, series.url)""",
                (info.source, info.source_id, info.name, info.summary, info.url,
                 json.dumps(extra, ensure_ascii=False, default=str, sort_keys=True)))
        return self.conn.execute("SELECT id FROM series WHERE source = ? AND source_id = ?",
                                 (info.source, info.source_id)).fetchone()["id"]

    def series_overview(self, terms: Iterable[str] = ()) -> list[dict[str, Any]]:
        """Every series with what you have of it and, when its page was read, what is missing.

        Rows have ``id``, ``name``, ``url``, ``owned`` (albums with a file), ``known`` (albums the source
        lists, ``None`` when no series page was read), ``missing`` (those you have no file for) and ``extra``
        (what else the series page said).
        ``terms`` must all appear in the name (case and accents ignored).
        """
        wanted = [fold(t) for t in terms]
        rows = []
        for series in self.conn.execute("SELECT * FROM series ORDER BY name"):
            if not all(t in fold(series["name"]) for t in wanted):
                continue
            owned = {r["source_id"] for r in self.conn.execute(
                """SELECT DISTINCT a.source_id FROM albums a JOIN files f ON f.album_id = a.id
                   WHERE a.series_id = ?""", (series["id"],))}
            if not owned and not self.conn.execute("SELECT 1 FROM albums WHERE series_id = ?", (series["id"],)).fetchone():
                continue  # a series nothing is linked to
            known = json.loads(series["extra"]).get("albums")
            missing = None if known is None else [a for a in known if str(a["source_id"]) not in owned]
            rows.append({"id": series["id"], "name": series["name"], "url": series["url"], "owned": len(owned),
                         "known": None if known is None else len(known), "missing": missing,
                         "extra": {k: v for k, v in json.loads(series["extra"]).items() if k != "albums"}})
        return rows

    # ---------------------------------------------------------------- albums
    def add_album(self, info: AlbumInfo) -> int:
        """Insert or update an album (identified by source + source_id).

        Values the user changed with :meth:`edit_album` are kept on top of what the source says.
        """
        existing = self.find_album(info.source, info.source_id)
        edits = self._edits_of(existing) if existing is not None else {}
        if edits:
            info = self._apply_edits(info, edits)
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

    # ------------------------------------------------------------ hand edits
    EDIT_SCALARS = ("title", "volume", "alternate_number", "publisher", "isbn", "year", "month", "day",
                    "pages", "language", "format", "summary")
    EDIT_FIELDS = EDIT_SCALARS + ("series", "credits", "genres")

    def _edits_of(self, album_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT extra FROM albums WHERE id = ?", (album_id,)).fetchone()
        return json.loads(row["extra"]).get("edits", {}) if row else {}

    @staticmethod
    def _apply_edits(info: AlbumInfo, edits: dict[str, Any]) -> AlbumInfo:
        """``info`` with the user's edits laid over it (a new object; ``info`` is untouched)."""
        changes: dict[str, Any] = {key: edits[key] for key in Library.EDIT_SCALARS if key in edits}
        if "series" in edits and info.series is not None:
            changes["series"] = dataclasses.replace(info.series, name=edits["series"])
        if "credits" in edits:
            credits = {**info.credits, **edits["credits"]}
            changes["credits"] = {role: names for role, names in credits.items() if names}
        if "genres" in edits:
            changes["genres"] = list(edits["genres"])
        changes["extra"] = {**info.extra, "edits": edits}
        return dataclasses.replace(info, **changes)

    def edit_album(self, album_id: int, changes: dict[str, Any]) -> None:
        """Change an album by hand; the change survives re-imports, ``--recache`` and ``reparse``.

        ``changes`` may hold any of :attr:`EDIT_SCALARS` (``None`` clears a value), ``series`` (renames
        the series, so every album of it), ``credits`` (``{role: [names]}``: the listed roles are
        replaced, an empty list removes the role) and ``genres`` (the complete list).
        """
        album = self.get_album(album_id)
        if album is None:
            raise KeyError(f"no album {album_id}")
        unknown = set(changes) - set(self.EDIT_FIELDS)
        if unknown:
            raise ValueError(f"can not edit: {', '.join(sorted(unknown))}")
        if "series" in changes and album["kind"] != "series":
            raise ValueError("a one-shot has no series to rename")
        edits = dict(album["extra"].get("edits", {}))
        with self.conn:
            columns = {k: changes[k] for k in self.EDIT_SCALARS if k in changes and k != "publisher"}
            if columns:
                self.conn.execute("UPDATE albums SET " + ", ".join(f"{c} = ?" for c in columns) + " WHERE id = ?",
                                  [*columns.values(), album_id])
            if "publisher" in changes:
                name = changes["publisher"]
                self.conn.execute("UPDATE albums SET publisher_id = ? WHERE id = ?",
                                  (self._get_or_create("publishers", name) if name else None, album_id))
            for role, names in (changes.get("credits") or {}).items():
                self.conn.execute(
                    "DELETE FROM album_credits WHERE album_id = ? AND role = ?", (album_id, role))
                for name in names:
                    self.conn.execute("INSERT OR IGNORE INTO album_credits(album_id, person_id, role) VALUES (?, ?, ?)",
                                      (album_id, self._get_or_create("people", name), role))
                edits.setdefault("credits", {})[role] = list(names)
            if "genres" in changes:
                self.conn.execute("DELETE FROM album_genres WHERE album_id = ?", (album_id,))
                for genre in changes["genres"]:
                    self.conn.execute("INSERT OR IGNORE INTO album_genres(album_id, genre_id) VALUES (?, ?)",
                                      (album_id, self._get_or_create("genres", genre)))
                edits["genres"] = list(changes["genres"])
            for key in self.EDIT_SCALARS:
                if key in changes:
                    edits[key] = changes[key]
            self._store_edits(album_id, edits)
            if "series" in changes:
                self._rename_series(album, changes["series"])

    def _store_edits(self, album_id: int, edits: dict[str, Any]) -> None:
        row = self.conn.execute("SELECT extra FROM albums WHERE id = ?", (album_id,)).fetchone()
        extra = json.loads(row["extra"])
        extra["edits"] = edits
        self.conn.execute("UPDATE albums SET extra = ? WHERE id = ?",
                          (json.dumps(extra, ensure_ascii=False, default=str, sort_keys=True), album_id))

    def _rename_series(self, album: dict[str, Any], name: str) -> None:
        series_id = self.conn.execute("SELECT series_id FROM albums WHERE id = ?", (album["id"],)).fetchone()[0]
        self.conn.execute("UPDATE series SET name = ? WHERE id = ?", (name, series_id))
        for row in self.conn.execute("SELECT id FROM albums WHERE series_id = ?", (series_id,)).fetchall():
            self._store_edits(row["id"], {**self._edits_of(row["id"]), "series": name})

    def series_albums(self, album_id: int) -> list[int]:
        """Ids of all the albums in the same series as ``album_id`` (just itself for a one-shot)."""
        rows = self.conn.execute(
            "SELECT id FROM albums WHERE series_id = (SELECT series_id FROM albums WHERE id = ?) "
            "AND series_id IS NOT NULL", (album_id,)).fetchall()
        return [r["id"] for r in rows] or [album_id]

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

    def series_of_album(self, album_id: int) -> Optional[tuple[str, str]]:
        """``(the series' identity at its source, its name)`` for an album of a series; ``None`` for a one-shot."""
        row = self.conn.execute("SELECT s.source_id, s.name FROM albums a JOIN series s ON s.id = a.series_id WHERE a.id = ?",
                                (album_id,)).fetchone()
        return (row["source_id"], row["name"]) if row else None

    def files_for_album(self, album_id: int) -> list[dict[str, Any]]:
        return sorted((self._file_row(r) for r in self.conn.execute(
            "SELECT * FROM files WHERE album_id = ?", (album_id,))), key=lambda r: r["path"])

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
               f"WHERE f.path != ? AND ((a.source = ? AND a.source_id = ?) OR {same_keys})")
        rows = self.conn.execute(sql, [self._store(exclude_path), source, source_id, *params])
        return sorted((self._file_row(r) for r in rows), key=lambda r: r["path"])

    def files_with_md5(self, md5: str) -> list[dict[str, Any]]:
        return sorted((self._file_row(r) for r in self.conn.execute("SELECT * FROM files WHERE md5 = ?", (md5,))),
                      key=lambda r: r["path"])

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
        merged = AlbumInfo(source=info.source, source_id=info.source_id, series=series, credits=credits,
                           genres=info.genres or old["genres"], extra=extra,
                           publisher=values.pop("publisher"), **values)
        edits = old["extra"].get("edits")
        return self._apply_edits(merged, edits) if edits else merged

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
        info, md5 = analyse_file(path)
        return self.add_analysed(info, md5, album_id)

    def add_analysed(self, info, md5: str, album_id: Optional[int] = None) -> int:
        """Register a file that was already inspected and hashed (see :func:`analyse_file`)."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO files(path, album_id, md5, size, format, page_count,
                                     corrupted, has_metadata, mtime, embedded)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       review = CASE WHEN excluded.album_id IS NOT NULL AND excluded.album_id IS NOT files.album_id
                                     THEN NULL ELSE files.review END,
                       album_id = COALESCE(excluded.album_id, files.album_id),
                       md5 = excluded.md5, size = excluded.size, format = excluded.format,
                       page_count = excluded.page_count, corrupted = excluded.corrupted,
                       has_metadata = excluded.has_metadata, mtime = excluded.mtime,
                       embedded = excluded.embedded, checked = CURRENT_TIMESTAMP""",
                (self._store(info.path), album_id, md5, info.size, info.format, info.page_count,
                 int(info.corrupted), int(info.has_metadata), info.mtime,
                 json.dumps(info.embedded, ensure_ascii=False) if info.embedded else None))
        return self.conn.execute("SELECT id FROM files WHERE path = ?",
                                 (self._store(info.path),)).fetchone()["id"]

    def flag_review(self, path, reason: str) -> None:
        """Mark a file for `bdnex review`, with the reason."""
        with self.conn:
            self.conn.execute("UPDATE files SET review = ? WHERE path = ?", (reason, self._store(path)))

    def clear_review(self, path) -> None:
        with self.conn:
            self.conn.execute("UPDATE files SET review = NULL WHERE path = ?", (self._store(path),))

    def set_cover_file(self, album_id: int, cover_file: Optional[str]) -> None:
        """Record where the album's cover is kept (a path below ``cover.dir``; ``None`` forgets it)."""
        with self.conn:
            self.conn.execute("UPDATE albums SET cover_file = ? WHERE id = ?", (cover_file, album_id))

    def file_by_id(self, file_id: int) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return self._file_row(row) if row else None

    def get_file(self, path) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM files WHERE path = ?", (self._store(path),)).fetchone()
        return self._file_row(row) if row else None

    def refresh_file(self, path) -> int:
        """Recompute size/md5/... after a file was modified (e.g. tags written)."""
        return self.add_file(path)

    def move_file(self, old_path, new_path) -> None:
        """Record that a file moved/was renamed, and refresh its checksum."""
        old_path, new_path = os.path.abspath(old_path), os.path.abspath(new_path)
        with self.conn:
            cur = self.conn.execute("UPDATE files SET path = ? WHERE path = ?",
                                    (self._store(new_path), self._store(old_path)))
        if cur.rowcount == 0:
            raise KeyError(f"{old_path} is not in the library")
        self.refresh_file(new_path)
        if self.root and new_path.startswith(self.root + os.sep):
            self.unstage_file(new_path)  # it is in the library now: nothing is waiting to be moved

    def set_file_album(self, path, album_id: Optional[int]) -> None:
        with self.conn:
            self.conn.execute("UPDATE files SET album_id = ?, review = NULL WHERE path = ?",
                              (album_id, self._store(path)))

    def remove_file(self, path) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE path = ?", (self._store(path),))

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
            groups.append(sorted((self._file_row(r) for r in self.conn.execute(
                "SELECT * FROM files WHERE md5 = ?", (row["md5"],))), key=lambda r: r["path"]))
        return groups

    # ---------------------------------------------------------------- query
    def query(self, terms: list[str], files: bool = False) -> list[dict[str, Any]]:
        """Albums (or files with ``files=True``) matching the query terms. See ``library.query``."""
        rows = run_query(self.conn, terms, files_mode=files)
        if not files:
            return rows
        return sorted((self._file_row(r) for r in rows), key=lambda r: r["path"])  # stored paths do not sort like real ones

    def album_matches(self, album_id: int, terms: list[str]) -> bool:
        """Does this album satisfy all the query ``terms`` (same syntax as ``bdnex ls``)?"""
        compiled = compile_query(terms)
        sql = (f"SELECT 1 FROM albums a {FROM_JOINS} WHERE a.id = ? AND ({compiled.where})")
        return self.conn.execute(sql, [album_id, *compiled.params]).fetchone() is not None

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

    def cache_delete(self, source: str, kind: str, url: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM source_cache WHERE source = ? AND kind = ? AND url = ?", (source, kind, url))

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
                (self._store(path), outcome, score, album_id))

    def forget_last_import(self, path) -> None:
        """Delete the newest import-log row of a file (used to take a decision back)."""
        with self.conn:
            self.conn.execute("DELETE FROM import_log WHERE id = (SELECT MAX(id) FROM import_log WHERE path = ?)",
                              (self._store(path),))

    def delete_album_if_unused(self, album_id: int) -> bool:
        """Delete an album no file is linked to. Returns whether it was deleted."""
        with self.conn:
            if self.conn.execute("SELECT 1 FROM files WHERE album_id = ?", (album_id,)).fetchone():
                return False
            return self.conn.execute("DELETE FROM albums WHERE id = ?", (album_id,)).rowcount > 0

    def last_import(self, path) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM import_log WHERE path = ? ORDER BY id DESC LIMIT 1",
            (self._store(path),)).fetchone()
        return self._file_row(row) if row else None

    # ------------------------------------------- files without an album, moved files
    def unmatched(self, terms: Iterable[str] = ()) -> list[dict[str, Any]]:
        """Files with no album: registered but not linked, or skipped / failed at the last import.

        ``terms`` must all appear in the path (case and accents ignored). Rows have ``path``,
        ``outcome`` (``untagged``, ``skipped`` or ``failed``), ``score`` (best match, when known)
        and ``at``.
        """
        rows = [self._file_row(r) for r in self.conn.execute(
            """SELECT l.path, l.outcome, l.score, l.at FROM import_log l
               JOIN (SELECT path, MAX(id) AS last FROM import_log GROUP BY path) m ON m.last = l.id
               WHERE l.outcome IN ('skipped', 'failed')
                 AND NOT EXISTS (SELECT 1 FROM files f WHERE f.path = l.path AND f.album_id IS NOT NULL)""")]
        seen = {r["path"] for r in rows}
        for r in self.conn.execute("SELECT path, checked AS at FROM files WHERE album_id IS NULL"):
            path = self._resolve(r["path"])
            if path not in seen:
                rows.append({"path": path, "outcome": "untagged", "score": None, "at": r["at"]})
        wanted = [fold(t) for t in terms]
        rows = [r for r in rows if all(t in fold(r["path"]) for t in wanted)]
        return sorted(rows, key=lambda r: r["path"])

    def missing_files(self) -> list[dict[str, Any]]:
        """Registered files that are no longer on disk (moved, renamed or deleted outside bdnex)."""
        rows = (self._file_row(r) for r in self.conn.execute("SELECT * FROM files"))
        return sorted((r for r in rows if not os.path.exists(r["path"])), key=lambda r: r["path"])

    def relink(self, folders: list[str], apply: bool = True, workers: int = 1) -> dict[str, Any]:
        """Find files that were moved or renamed outside bdnex: look in ``folders`` for a file with the same
        content (size, then md5) as each missing one and point the library at it. Returns
        ``{"found": [(old, new)], "lost": [old...], "ambiguous": [(old, [new...])]}``; a file whose copy is
        already registered, or that has several copies, is left alone."""
        missing = self.missing_files()
        by_size: dict[int, list[dict[str, Any]]] = {}
        for row in missing:
            by_size.setdefault(row["size"], []).append(row)
        registered = {row["path"] for row in (self._file_row(r) for r in self.conn.execute("SELECT path FROM files"))}
        candidates: dict[str, list[str]] = {}  # md5 -> paths on disk
        same_size = []
        for folder in folders:
            for path in iter_comic_files(os.path.abspath(os.path.expanduser(folder))):
                if path in registered:
                    continue
                try:
                    if os.path.getsize(path) in by_size:
                        same_size.append(path)
                except OSError:
                    continue
        from bdnex.parallel import map_ordered

        for path, digest in map_ordered(file_md5, same_size, workers):  # only files of a size a missing one had are read
            if not isinstance(digest, Exception):
                candidates.setdefault(digest, []).append(path)
        result: dict[str, Any] = {"found": [], "lost": [], "ambiguous": []}
        for row in missing:
            paths = sorted(set(candidates.get(row["md5"], [])))
            if not paths:
                result["lost"].append(row["path"])
            elif len(paths) > 1:
                result["ambiguous"].append((row["path"], paths))
            else:
                result["found"].append((row["path"], paths[0]))
        if apply:
            used = set()
            for old, new in result["found"]:
                if new in used:  # two missing rows with the same content: the first one takes the file
                    continue
                used.add(new)
                with self.conn:
                    self.conn.execute("UPDATE files SET path = ? WHERE path = ?", (self._store(new), self._store(old)))
                    self.conn.execute("UPDATE import_log SET path = ? WHERE path = ?", (self._store(new), self._store(old)))
        return result

    def relocate(self, old: str, new: str, apply: bool = True) -> dict[str, Any]:
        """Replace the folder prefix ``old`` by ``new`` in every file path (and the import log).

        Files under the library root are stored relative to it and follow it when ``collection_path``
        changes, so this is for the others (and for databases kept with absolute paths). Returns
        ``{"files": n, "log": n, "collisions": [...], "moved": [(old, new), ...]}`` in absolute paths.
        Nothing is changed when a new path is already registered (``collisions``) or when ``apply``
        is false.
        """
        old, new = os.path.abspath(os.path.expanduser(old)), os.path.abspath(os.path.expanduser(new))
        for alias, folder in self.roots.items():  # a registered source: its files follow one changed row
            if folder == old.rstrip("/"):
                if apply:
                    with self.conn:
                        self.conn.execute("UPDATE roots SET path = ? WHERE alias = ?", (new.rstrip("/"), alias))
                    self._load_roots()
                count = self.conn.execute("SELECT COUNT(*) FROM files WHERE path LIKE ?", (f"@{alias}/%",)).fetchone()[0]
                return {"files": count, "log": 0, "collisions": [], "moved": [], "source": alias}

        def rewrite(path: str) -> Optional[str]:
            if path == old:
                return new
            return new + path[len(old):] if path.startswith(old.rstrip("/") + "/") else None

        files = [(r["path"], self._resolve(r["path"])) for r in self.conn.execute("SELECT path FROM files")]
        moved = [(stored, rewrite(path)) for stored, path in files if rewrite(path)]
        moving = {stored for stored, _ in moved}
        taken = {stored for stored, _ in files} - moving
        collisions = [target for _, target in moved if self._store(target) in taken]
        logged = [(r["id"], rewrite(self._resolve(r["path"]))) for r in self.conn.execute("SELECT id, path FROM import_log")
                  if rewrite(self._resolve(r["path"]))]
        shown = [(self._resolve(stored), target) for stored, target in moved]
        result = {"files": len(moved), "log": len(logged), "collisions": collisions, "moved": shown}
        if apply and not collisions:
            with self.conn:
                for stored, target in moved:
                    self.conn.execute("UPDATE files SET path = ? WHERE path = ?", (self._store(target), stored))
                for log_id, target in logged:
                    self.conn.execute("UPDATE import_log SET path = ? WHERE id = ?", (self._store(target), log_id))
        return result

    # ---------------------------------------------------- spare versions, staging
    FILE_FIELDS = frozenset({"spare", "cover_hash", "cover_score"})

    def set_file_fields(self, path, **fields: Any) -> None:
        """Set ``spare`` (an extra version of its album), ``cover_hash`` or ``cover_score``."""
        unknown = set(fields) - self.FILE_FIELDS
        if unknown:
            raise ValueError(f"can not set: {', '.join(sorted(unknown))}")
        if not fields:
            return
        with self.conn:
            cur = self.conn.execute("UPDATE files SET " + ", ".join(f"{c} = ?" for c in fields) + " WHERE path = ?",
                                    [*fields.values(), self._store(path)])
        if cur.rowcount == 0:
            raise KeyError(f"{path} is not in the library")

    def stage_file(self, path, origin: Optional[str] = None) -> None:
        """Remember that a registered file was imported where it is and still has to be moved in."""
        row = self.get_file(path)
        if row is None:
            raise KeyError(f"{path} is not in the library")
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO staged_files(file_id, origin) VALUES (?, ?)",
                              (row["id"], os.path.abspath(origin or path)))

    def unstage_file(self, path) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM staged_files WHERE file_id = (SELECT id FROM files WHERE path = ?)",
                              (self._store(path),))

    def staged_files(self) -> list[dict[str, Any]]:
        """Files imported without being moved into the library, with the path they came from."""
        rows = self.conn.execute(
            "SELECT f.*, s.origin, s.staged FROM staged_files s JOIN files f ON f.id = s.file_id")
        return sorted((self._file_row(r) for r in rows), key=lambda r: r["path"])

    # ------------------------------------------------------------------ trash
    def add_trash(self, path, trash_path, reason: str, album_id: Optional[int] = None, md5: Optional[str] = None,
                  size: Optional[int] = None, fmt: Optional[str] = None) -> int:
        """Record a file that was moved to the trash (``trash_path`` is where it is now)."""
        with self.conn:
            return self.conn.execute(
                "INSERT INTO trash(path, trash_path, album_id, md5, size, format, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self._store(path), self._store(trash_path), album_id, md5, size, fmt, reason)).lastrowid

    def trash_entries(self, older_than_days: Optional[int] = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM trash", []
        if older_than_days is not None:
            sql += " WHERE trashed <= datetime('now', ?)"
            params.append(f"-{int(older_than_days)} days")
        rows = self.conn.execute(sql + " ORDER BY id", params)
        entries = []
        for row in rows:
            entry = dict(row)
            entry["path"], entry["trash_path"] = self._resolve(entry["path"]), self._resolve(entry["trash_path"])
            entries.append(entry)
        return entries

    def get_trash(self, trash_id: int) -> Optional[dict[str, Any]]:
        return next((e for e in self.trash_entries() if e["id"] == trash_id), None)

    def delete_trash(self, trash_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM trash WHERE id = ?", (trash_id,))

    # ------------------------------------------------------------- moves (undo)
    def record_move(self, batch: str, old_path, new_path) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO operations(batch, old_path, new_path) VALUES (?, ?, ?)",
                              (batch, self._store(old_path), self._store(new_path)))

    def batches(self) -> list[dict[str, Any]]:
        """The runs that moved files, newest first: ``batch``, ``at``, ``moves`` and ``undone`` counts."""
        rows = self.conn.execute(
            "SELECT batch, MIN(at) AS at, COUNT(*) AS moves, SUM(undone) AS undone FROM operations "
            "GROUP BY batch ORDER BY MAX(id) DESC")
        return [dict(row) for row in rows]

    def moves_of(self, batch: str, pending_only: bool = True) -> list[dict[str, Any]]:
        """The moves of one run, newest first (the order to take them back in), with absolute paths."""
        sql = "SELECT * FROM operations WHERE batch = ?" + (" AND undone = 0" if pending_only else "") + " ORDER BY id DESC"
        moves = []
        for row in self.conn.execute(sql, (batch,)):
            move = dict(row)
            move["old_path"], move["new_path"] = self._resolve(move["old_path"]), self._resolve(move["new_path"])
            moves.append(move)
        return moves

    def mark_undone(self, operation_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE operations SET undone = 1 WHERE id = ?", (operation_id,))

    # ------------------------------------------------------------------- meta
    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: Optional[str] = None) -> None:
        """Store a value (the current UTC time, as the import log writes it, when ``value`` is None)."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, COALESCE(?, CURRENT_TIMESTAMP)) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def delete_meta(self, key: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM meta WHERE key = ?", (key,))

    # ------------------------------------------------------------------ reset
    def reset(self, apply: bool = True) -> dict[str, int]:
        """Forget everything about your collection, keep what was downloaded.

        Deletes the files, albums, series, credits, genres, publishers (with your hand edits) and
        the import log. The ``source_cache`` (sitemaps, album and series pages, covers) stays, so
        importing again needs no network for what was already fetched. Files on disk are not
        touched. Returns what is (or would be) deleted, by table.
        """
        tables = ("import_log", "files", "album_genres", "album_credits", "albums", "series", "genres",
                  "people", "publishers")  # children first
        counts = {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        if apply:
            with self.conn:
                for table in tables:
                    self.conn.execute(f"DELETE FROM {table}")
            self.conn.execute("VACUUM")
        return counts

    def cache_summary(self) -> dict[str, tuple[int, int]]:
        """Cached entries by kind: ``{"sitemap": (count, bytes), ...}``."""
        return {r["kind"]: (r["n"], r["size"] or 0) for r in self.conn.execute(
            "SELECT kind, COUNT(*) AS n, SUM(LENGTH(content)) AS size FROM source_cache GROUP BY kind")}

    def backup_to(self, destination) -> str:
        """A consistent copy of the whole database (cache included) at ``destination``."""
        destination = os.path.expanduser(str(destination))
        copy = sqlite3.connect(destination)
        try:
            self.conn.backup(copy)
        finally:
            copy.close()
        return destination

    # ----------------------------------------------------------------- stats
    def people_summary(self, text: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """The people credited in the library (each name is one row of ``people``), most albums first, with the albums
        they have by role. ``text`` filters by name (case and accents do not matter)."""
        like = "%" + fold(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = "WHERE fold(p.name) LIKE ? ESCAPE '\\'"
        total = self.conn.execute(f"SELECT COUNT(*) FROM people p {where} AND EXISTS (SELECT 1 FROM album_credits c WHERE c.person_id = p.id)",
                                  (like,)).fetchone()[0]
        rows = self.conn.execute(
            f"SELECT p.id, p.name, COUNT(DISTINCT c.album_id) AS albums FROM people p JOIN album_credits c ON c.person_id = p.id {where} "
            "GROUP BY p.id ORDER BY albums DESC, p.name LIMIT ? OFFSET ?", (like, limit, offset)).fetchall()
        items = [{"id": r["id"], "name": r["name"], "albums": r["albums"], "roles": {}} for r in rows]
        by_id = {item["id"]: item for item in items}
        if by_id:
            marks = ",".join("?" * len(by_id))
            for r in self.conn.execute(f"SELECT person_id, role, COUNT(*) AS n FROM album_credits WHERE person_id IN ({marks}) GROUP BY person_id, role", list(by_id)):
                by_id[r["person_id"]]["roles"][r["role"]] = r["n"]
        return {"count": total, "items": items}

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
