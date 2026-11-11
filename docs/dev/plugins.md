# Metadata source plugins *(planned)*

A plugin turns one website into the common format below. The first plugin,
`bedetheque`, lives in `bdnex/importer/bedetheque/` and is written as a
self-contained package so it can later move to its own project.

## Contract

A plugin provides three things:

1. **candidates**: given a file name, a list of `(source_id, title, url)` scored
   locally (no network).
2. **fetch**: given a candidate, download its pages. Every response is stored
   with `Library.cache_put(source, kind, url, content)` and read back with
   `Library.cache_get`, so a second import makes no request.
3. **parse**: turn cached pages into an {py:class}`bdnex.library.AlbumInfo`.

The importer then calls `Library.add_album(info)`, which is the only write
endpoint a plugin needs.

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

## Enabling a plugin

```yaml
plugins: [bedetheque]
bedetheque:
  delay: [3, 10]       # seconds between requests
```
