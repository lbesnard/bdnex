# Metadata source plugins

A *source* turns one website or database into
{py:class}`bdnex.library.AlbumInfo` objects. The importer does everything else: it asks the source for
candidates, scores them, asks you when unsure, fetches the chosen album, stores it in the library and
links the file. A source never writes to the library itself (except for its response cache).

Bédéthèque is the built-in source. Anyone can add another one, in or outside this repository.

## Choosing a source

```yaml
source: bedetheque                       # the default
```

`source` can be:

| Value | Meaning |
|---|---|
| `bedetheque` | a built-in source |
| a name | a plugin installed with an entry point in the `bdnex.sources` group |
| `package.module:factory` | a factory found on the Python path, no installation needed |

Options of a third-party source go under `plugins:`, keyed by the source's name. They are free-form
and bdnex does not interpret them:

```yaml
source: mysource
plugins:
  mysource:
    api_key: "..."
```

## The contract

A source is created by a **factory** called as `factory(library, settings)`, where `library` is the
open {py:class}`bdnex.library.Library` and `settings` the validated configuration
(`settings.plugins["mysource"]` holds its options). It returns an object with these members
({py:class}`bdnex.importer.source.MetadataSource`):

| Member | Role |
|---|---|
| `name` | short unique name; becomes `AlbumInfo.source`, so `(name, source_id)` identifies an album |
| `fetch_errors` | tuple of exception types meaning "this page can't be fetched now" |
| `is_ready()` | `True` when the local data used for matching exists (e.g. a downloaded index) |
| `prepare(recache=False, progress=None)` | download or refresh that data; `progress(message)` shows a status line |
| `candidates(query, limit=5)` | `[(Candidate, score 0-100)]` for a file name. **No network access** |
| `album_info(url, recache=False)` | the full {py:class}`~bdnex.library.AlbumInfo`; raise one of `fetch_errors` if unavailable |
| `partial_info(candidate)` | what the candidate alone tells (must set `extra={"partial": True}`) |

A {py:class}`bdnex.importer.source.Candidate` is `(source_id, url, name, volume)`: `name` is the
normalised text compared with the file name, and `volume` is `None` for one-shots and specials.

How the importer uses them: `bdnex init` calls `prepare(recache=True)` (and `import` offers to when `is_ready()` is false); for each
file it takes `candidates`, re-ranks them with the volume found in the file name and applies the
configured thresholds; for the chosen one it calls `album_info`. If that raises one of `fetch_errors`
the album is stored from `partial_info` and reported as *matched*; a later import with the page
available upgrades it in place.

## What to put in `AlbumInfo`

- Leave `series=None` for a one-shot; give a `SeriesInfo` for a numbered album.
- Credits use the ComicInfo vocabulary (`Writer`, `Penciller`, `Inker`, `Colorist`, `Letterer`, `CoverArtist`).
- Put fields you don't map in `extra`.

```{eval-rst}
.. autoclass:: bdnex.library.AlbumInfo
   :members:
.. autoclass:: bdnex.library.SeriesInfo
   :members:
.. autoclass:: bdnex.importer.source.Candidate
   :members:
```

## The cache

Store what you download with `library.cache_put(source, kind, url, content)` and read it back with
`cache_get`. Every later import then makes no request, and `--fetch --force` calls `album_info`
with `recache=True` so you know to refetch. A response that is not the page (an error or a
challenge page) must never be cached.

## A minimal source

```python
# mysource.py
from bdnex.importer.source import Candidate
from bdnex.library import AlbumInfo


class MySource:
    name = "mysource"
    fetch_errors = (KeyError,)

    def __init__(self, library, options):
        self.library, self.options = library, options

    def is_ready(self):
        return True

    def prepare(self, recache=False, progress=None):
        pass

    def candidates(self, query, limit=5):
        return [(Candidate("1", "https://example.org/albums/1", "maus"), 90.0)]

    def album_info(self, url, recache=False):
        return AlbumInfo("mysource", "1", "Maus", credits={"Writer": ["Spiegelman, Art"]})

    def partial_info(self, candidate):
        return AlbumInfo("mysource", candidate.source_id, candidate.name, extra={"partial": True})


def create_source(library, settings):
    return MySource(library, settings.plugins.get("mysource", {}))
```

Try it without installing anything by putting `mysource.py` on the Python path and setting
`source: "mysource:create_source"`.

## Distributing a plugin

Put the factory in your own package and declare it:

```toml
[project.entry-points."bdnex.sources"]
mysource = "mysource:create_source"
```

After `pip install`, `source: mysource` works. A built-in source with the same name always wins.

## The Bédéthèque source

`bdnex.importer.bedetheque`:

- The sitemap files (55 or so, growing) are downloaded until the site serves an empty one, then matched
  locally with `rapidfuzz`. The URL slug already tells series from one-shot (`-Tome-N-`).
- Album pages come from a *page fetcher*: any callable `url -> bytes`, selected by
  `bedetheque.fetcher`. The default reads pages you saved from your browser into `bedetheque.pages_dir`.
- Sitemaps wait `bedetheque.sitemap_delay` between files; pages wait `bedetheque.delay`.
- Both the current desktop page layout (schema.org microdata) and the former mobile layout are parsed.

bedetheque.com answers scripted page requests with an anti-bot challenge. `http_fetch` raises
`BlockedError` and caches nothing; bdnex does not try to bypass it. See *Album pages* in
[Getting started](../guides/getting-started.md).


## Optional: `warm()`

The importer matches file names in worker processes forked from the main one. If your source
has a slow local index to build, add a `warm(self) -> None` method that builds it: the importer
calls it once before forking, and the workers share the result instead of each building their
own. `candidates` is called from those workers, so it must not use the network or the database.

## Optional: `catalogue()`

`catalogue(self) -> list[Candidate]` returns every album the source knows locally (no network). When it
exists, the user can search the whole list by hand from the match question, with `fzf` if it is installed.
Without it the search is limited to the best hits of `candidates` for the words typed.

## Optional: `album_count()`

`bdnex init` shows a progress bar while `prepare` runs and then reports how many albums the list
holds and how many are new. If your source has such a list, add `album_count(self) -> int` (the
albums known locally, 0 when none) and, for the bar, `sitemaps_cached(self) -> int` (how many files
a refresh will go through). Both are optional; without them `init` still works, with a rough bar.

## Optional: `cover_image(url, recache=False)`

For `cover.check` the importer asks the source for an album's cover with `cover_image(url)` (the
`cover_url` you stored in `AlbumInfo`): the image bytes, or `None`. Do it through `library.cache_get` /
`cache_put` so each cover is requested once. Without it the check is skipped for that source.

