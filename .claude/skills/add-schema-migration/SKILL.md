---
name: add-schema-migration
description: Add a column or table to the bdnex library database without breaking existing libraries. Use when touching bdnex/library/schema.py or adding anything stored in the SQLite library.
---

1. Append a new string to `MIGRATIONS` in `bdnex/library/schema.py` (`ALTER TABLE ... ADD COLUMN`, or `CREATE TABLE`).
   Never edit an earlier entry: `SCHEMA_VERSION` is `len(MIGRATIONS)`, and libraries migrate in place.
2. Expose it in `bdnex/library/database.py` (a small method, e.g. `set_...`) and, if files or albums are queried,
   in `bdnex/library/query.py` (`FILE_COLUMNS` / `ALBUM_COLUMNS`, plus a field in `ALL_FIELDS` when it can be searched).
   A column that is JSON is decoded in `_file_row`.
3. Document it: `docs/dev/library.md` (every table; the docs test checks tables), `docs/guides/queries.md` (every
   query field), and the guide of the feature.
4. Tests: `test/library/` for the method, and a test that the new value survives what rewrites the row
   (`add_file` upserts, `reset`, `merge_album`).
5. In the PR body: "schema vN: back up the database first, an older bdnex will refuse it".
