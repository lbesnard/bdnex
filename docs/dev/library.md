# Library database

The library is a single SQLite file (`library:` in the configuration). It is an
**index**: it can be rebuilt by rescanning the files, which carry their own
`ComicInfo.xml`.


## Tables

| Table | Purpose |
|---|---|
| `files` | one row per comic file: path (relative to `directory` when under it, absolute otherwise; the `Library` API always takes and returns absolute paths), md5, size, format, page count, `corrupted`, `has_metadata`, link to an album, `spare` (an extra version of its album), `cover_hash` (64-bit hash of the first page), `cover_score` (similarity with the album cover) |
| `albums` | `kind` is `series` or `oneshot`; a series album has a `series_id`, a one-shot has none (enforced by a `CHECK`) |
| `series` | name, summary, source id |
| `people`, `album_credits` | authors with a role (`Writer`, `Penciller`, `Colorist`, `Inker`, `Letterer`, `CoverArtist`) |
| `genres`, `album_genres` | many-to-many |
| `publishers` | |
| `source_cache` | every page and sitemap fetched, **stored as received**, by source: a re-import never hits the website, and `bdnex reparse` can rebuild the albums from it offline |
| `import_log` | outcome and score of each import, used for resume and for remembered skips |
| `trash` | files that were replaced: where they were, the album, checksum, size, why, and where they are now |
| `staged_files` | files imported without being moved into the library folder, with the path they came from |
| `meta` | small values: that paths are stored relative, when the album list last grew |

The schema version is stored in `PRAGMA user_version`; migrations live in
`bdnex/library/schema.py` and are append-only.

## What is kept

- The **raw pages** (`source_cache`): the complete record of what a source said, whether or not bdnex
  understands all of it yet.
- The **parsed albums**: columns for what is queried (`ls`), `album_credits` for every role the page
  names (including `Translator` and `Other`, which ComicInfo has no element for), and `extra`
  (JSON) for everything else: the whole edition table (`format`, `Poids`, `Achevé impr.`,
  `Estimation`...), the edition note, the vote count, the image links.
- Albums do not depend on files: removing or replacing a file never removes an album.

## Python API

```python
from bdnex.library import Library, AlbumInfo, SeriesInfo

with Library("~/.local/share/bdnex/bdnex.sqlite") as lib:
    album_id = lib.add_album(AlbumInfo(source="bedetheque", source_id="245127",
                                       title="Redwin de la Forge",
                                       series=SeriesInfo("bedetheque", "47467", "Nains"),
                                       volume=1, credits={"Writer": ["Jarry, Nicolas"]}))
    lib.add_file("/comics/nains-1.cbz", album_id)
    lib.refresh_file("/comics/nains-1.cbz")   # after tags were written: new md5
```

```{eval-rst}
.. autoclass:: bdnex.library.Library
   :members:
```
