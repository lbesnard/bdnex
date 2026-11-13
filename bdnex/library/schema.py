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
    # --- version 3 -------------------------------------------------------
    # spare versions of an album, the trash, files imported but not yet moved into the library,
    # and what is known of a file's cover.
    """
    ALTER TABLE files ADD COLUMN spare INTEGER NOT NULL DEFAULT 0;      -- 1: an extra version of its album
    ALTER TABLE files ADD COLUMN cover_hash TEXT;                       -- 64-bit dHash of the first page, hex
    ALTER TABLE files ADD COLUMN cover_score REAL;                      -- 0-100 similarity with the album cover

    CREATE TABLE trash (
        id         INTEGER PRIMARY KEY,
        path       TEXT NOT NULL,        -- where the file was (as stored in `files.path`)
        trash_path TEXT NOT NULL,        -- where it is now, relative to the library folder
        album_id   INTEGER REFERENCES albums(id) ON DELETE SET NULL,
        md5        TEXT,
        size       INTEGER,
        format     TEXT,
        reason     TEXT NOT NULL,
        trashed    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE staged_files (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        origin  TEXT NOT NULL,           -- the full path it was imported from
        staged  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # --- version 4 -------------------------------------------------------
    # What a series page says beyond its name and summary (its albums, and whatever else is parsed from it).
    """
    ALTER TABLE series ADD COLUMN extra TEXT NOT NULL DEFAULT '{}';
    """,
    # --- version 5 -------------------------------------------------------
    # What the file itself says (its ComicInfo.xml, as JSON), kept apart from the album it is linked to.
    """
    ALTER TABLE files ADD COLUMN embedded TEXT;
    """,
    # --- version 6 -------------------------------------------------------
    # The album's cover as a file next to the library (`cover.dir`): its path below that folder.
    # `cover_url` stays the address it came from.
    """
    ALTER TABLE albums ADD COLUMN cover_file TEXT;
    """,
    # --- version 7 -------------------------------------------------------
    # A file whose match should be looked at by hand (`bdnex review`): why, in words. NULL: nothing to review.
    """
    ALTER TABLE files ADD COLUMN review TEXT;
    """,
    # --- version 8 -------------------------------------------------------
    # Files that were moved or renamed by `move`, per run (`batch`), so that `bdnex undo` can put them back.
    # Paths are stored the way `files.path` is.
    """
    CREATE TABLE operations (
        id       INTEGER PRIMARY KEY,
        batch    TEXT NOT NULL,
        old_path TEXT NOT NULL,
        new_path TEXT NOT NULL,
        at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        undone   INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_operations_batch ON operations(batch);
    """,
    # --- version 9 -------------------------------------------------------
    # Import sources (folders whose files are copied or moved into the collection). A file under one is stored as
    # `@<alias>/<path below it>`: moving the folder is one UPDATE of `roots.path` (`bdnex relocate`). `origins`
    # remembers where a copied file came from, so a second import of the source skips what was already copied.
    """
    CREATE TABLE roots (
        alias TEXT PRIMARY KEY,
        path  TEXT NOT NULL UNIQUE,
        added TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE origins (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        origin  TEXT NOT NULL,           -- the path it was copied from, stored like `files.path`
        size    INTEGER,                 -- of the source file when it was copied
        mtime   REAL,
        copied  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_origins_origin ON origins(origin);
    """,
]
