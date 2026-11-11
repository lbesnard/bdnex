# Metadata source plugins

A plugin turns one website into the common format below. The first plugin,
`bedetheque`, lives in `bdnex/importer/bedetheque/` and is written as a
self-contained package so it can later move to its own project.

## Contract

A plugin is a class built on a {py:class}`bdnex.library.Library` (its cache) and
provides:

1. `candidates(name, limit)`: ranked `(Candidate, score 0-100)` for a file name,
   computed locally with no network access.
2. `album_info(url, recache=False)`: fetch the album (and series) pages, cache
   every response with `Library.cache_put(source, kind, url, content)` and
   return an {py:class}`bdnex.library.AlbumInfo`. A second call reads the cache
   and makes no request; `recache=True` refetches.
3. `cover(info, recache=False)`: the cover image bytes (cached the same way).

The importer then calls `Library.add_album(info)`, which is the only write
endpoint a plugin needs. The network and the sleeping are injectable, so tests
never touch the website.

## The Bédéthèque plugin

`bdnex.importer.bedetheque.Bedetheque`:

- `update_sitemaps(recache=False)` downloads the 47 sitemap files into the cache.
- Matching normalises the slug of every album URL (drops the id and French
  stopwords) and ranks them with `rapidfuzz`.
- An album is a **one-shot** when its page has no `Tome` number; a numbered
  `Tome`, or a special such as `Hors-série` or `INT 3` (stored as
  `alternate_number`), makes it part of a series.
- A random delay (default 3 to 10 s) is slept before each real request only.

```{eval-rst}
.. autoclass:: bdnex.library.AlbumInfo
   :members:
.. autoclass:: bdnex.library.SeriesInfo
   :members:
```

## Rules

- `source` is the plugin name; `(source, source_id)` identifies the album.
- Leave `series=None` for a one-shot.
- Credits use the ComicInfo vocabulary (`Writer`, `Penciller`, ...).
- Put source-specific fields you do not map in `extra`.
- Be polite: delay between requests, honour `--recache` only when asked.

## Enabling a plugin *(planned)*

```yaml
plugins: [bedetheque]
bedetheque:
  delay: [3, 10]       # seconds between requests
```
