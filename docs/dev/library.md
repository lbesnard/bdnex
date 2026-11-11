# Library database

The library is a single SQLite file (`library:` in the configuration). It is an
**index**: it can be rebuilt by rescanning the files, which carry their own
`ComicInfo.xml`.


## Tables

| Table | Purpose |
|---|---|
| `files` | one row per comic file: path, md5, size, format, page count, `corrupted`, `has_metadata`, link to an album |
| `albums` | `kind` is `series` or `oneshot`; a series album has a `series_id`, a one-shot has none (enforced by a `CHECK`) |
| `series` | name, summary, source id |
| `people`, `album_credits` | authors with a role (`Writer`, `Penciller`, `Colorist`, `Inker`, `Letterer`, `CoverArtist`) |
| `genres`, `album_genres` | many-to-many |
| `publishers` | |
| `source_cache` | raw pages and sitemaps by source, so a re-import never hits the website |
| `import_log` | outcome and score of each import, used for resume and skip |

The schema version is stored in `PRAGMA user_version`; migrations live in
`bdnex/library/schema.py` and are append-only.

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
