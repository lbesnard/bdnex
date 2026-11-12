"""Versioned schema. Each entry of MIGRATIONS upgrades the DB by one version.

Never edit a released migration: append a new one.
"""

MIGRATIONS = [
    # --- version 1 -------------------------------------------------------
    """
    CREATE TABLE publishers (
        id   INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE series (
        id        INTEGER PRIMARY KEY,
        source    TEXT NOT NULL,
        source_id TEXT NOT NULL,
        name      TEXT NOT NULL,
        summary   TEXT,
        url       TEXT,
        UNIQUE (source, source_id)
    );
    CREATE INDEX idx_series_name ON series(name);

    CREATE TABLE albums (
        id               INTEGER PRIMARY KEY,
        kind             TEXT NOT NULL CHECK (kind IN ('series', 'oneshot')),
        series_id        INTEGER REFERENCES series(id) ON DELETE RESTRICT,
        title            TEXT NOT NULL,
        volume           INTEGER,
        alternate_number TEXT,
        publisher_id     INTEGER REFERENCES publishers(id),
        isbn             TEXT,
        year             INTEGER,
        month            INTEGER,
        day              INTEGER,
        pages            INTEGER,
        language         TEXT,
        format           TEXT,
        summary          TEXT,
        rating           REAL,
        cover_url        TEXT,
        url              TEXT,
        source           TEXT NOT NULL,
        source_id        TEXT NOT NULL,
        extra            TEXT NOT NULL DEFAULT '{}',
        added            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (source, source_id),
        CHECK ((kind = 'series') = (series_id IS NOT NULL))
    );
    CREATE INDEX idx_albums_series ON albums(series_id);
    CREATE INDEX idx_albums_title ON albums(title);
    CREATE INDEX idx_albums_isbn ON albums(isbn);

    CREATE TABLE people (
        id   INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE album_credits (
        album_id  INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
        role      TEXT NOT NULL,
        PRIMARY KEY (album_id, person_id, role)
    );
    CREATE INDEX idx_credits_person ON album_credits(person_id);

    CREATE TABLE genres (
        id   INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE album_genres (
        album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
        genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE RESTRICT,
        PRIMARY KEY (album_id, genre_id)
    );

    CREATE TABLE files (
        id           INTEGER PRIMARY KEY,
        path         TEXT NOT NULL UNIQUE,
        album_id     INTEGER REFERENCES albums(id) ON DELETE SET NULL,
        md5          TEXT NOT NULL,
        size         INTEGER NOT NULL,
        format       TEXT NOT NULL,
        page_count   INTEGER,
        corrupted    INTEGER NOT NULL DEFAULT 0,
        has_metadata INTEGER NOT NULL DEFAULT 0,
        mtime        REAL,
        added        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        checked      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_files_md5 ON files(md5);
    CREATE INDEX idx_files_album ON files(album_id);

    CREATE TABLE source_cache (
        source  TEXT NOT NULL,
        kind    TEXT NOT NULL,          -- sitemap | album | series | cover
        url     TEXT NOT NULL,
        content BLOB,
        fetched TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (source, kind, url)
    );

    CREATE TABLE import_log (
        id       INTEGER PRIMARY KEY,
        path     TEXT NOT NULL,
        outcome  TEXT NOT NULL,         -- tagged | skipped | duplicate | failed
        score    REAL,
        album_id INTEGER REFERENCES albums(id) ON DELETE SET NULL,
        at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_import_log_path ON import_log(path);
    """,
    # --- version 2 -------------------------------------------------------
    # File paths under the library root are stored relative to it (`meta.paths = relative`). An
    # older bdnex would read them as relative to the current folder, so it must refuse this file.
    """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    INSERT INTO meta(key, value) VALUES ('paths', 'relative');
    """,
]
