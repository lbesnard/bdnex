# Editing and maintaining the library

## Link everything first, read the pages later

Matching needs no network. To link a whole collection quickly, without requesting a single album
page:

```bash
bdnex import --match --auto ~/comics        # confident matches are linked unasked
bdnex import --match --auto=all ~/comics    # strong and medium matches; never asks
```

Each file is linked to the album as the album list describes it (series, volume, title). The
albums are *matched*, not yet *complete*: `bdnex import --fetch` reads their pages later, slowly
and politely, and can be interrupted and resumed. See [Importing](importing.md).

## Series: what you have, and what is missing

A series page lists every album of the series. bdnex reads that list when it reads one of the series' albums
(`bdnex import --fetch`; the series page was already fetched for its summary, so there is no extra request) and keeps
it in the library, next to the albums you own:

```bash
bdnex series                    # every series: "Nains  3 of 26 albums  ·  missing 23"
bdnex series --missing          # only the series that lack albums
bdnex series nains --long       # the missing albums, with their address
bdnex series --json
```

With `--long`, `bdnex series` also shows what the series page said: genres, status, origin, language, rating.
bdnex does not know the layout of the page in advance: it **harvests everything the page carries**, whatever its
layout (the Open Graph / description tags, the schema.org values such as genre, rating and language, any
`label : value` row of a list or table, any JSON-LD block), and keeps it in the library with the series. A few
values get a stable name (`genres`, `status`, `origin`, `language`, `rating`, `image`); nothing is invented when
the page lacks it.

A series whose page was not read yet only shows what you own, until `bdnex import --fetch` reads it. The list comes
from the links of the page, which follow the same scheme in every layout, so it does not depend on how the page looks.

## Files without an album

```bash
bdnex ls --unmatched                 # skipped, failed, or registered without an album
bdnex ls --unmatched rahan           # only paths containing "rahan" (case and accents ignored)
bdnex ls --unmatched -c              # just count them
bdnex ls --unmatched --json
```

```text
skipped    62.0  /comics/Rahan/Rahan - 046 - Le retour des goraks.pdf
failed        -  /comics/Mag/broken.cbz
untagged      -  /comics/Misc/Loose.cbz
```

The number is how close the best candidate was when you skipped the file (`-` when there was
none). Only the **latest** import of a file counts: once it is linked, it leaves the list.
Re-import them with `bdnex import <folder>` (already linked files are skipped).

## Editing an album or a series

```bash
bdnex edit series:nains              # opens every matching album in $EDITOR (YAML)
bdnex edit 'Loup Noir'               # same query language as `bdnex ls`
bdnex edit series:nains --set publisher=Soleil --set year=2007 -y    # no editor
bdnex edit -n series:nains           # show the changes, store nothing
```

In the editor, each album is a block:

```yaml
# Nains - T01 - Redwin de la Forge
- id: 12
  series: Nains          # renames the series for ALL its albums
  volume: 1
  title: Redwin de la Forge
  publisher: Soleil
  year: 2007
  summary: |-
    Several lines
    are fine.
  credits:
    Writer: ['Jarry, Nicolas']
    Translator: []        # removes the role
  genres: [Fantasy, Humour]
```

- Leave a line alone, or delete it, to keep the value. `null` (or an empty value) clears it.
- A credit role you list is **replaced** by what you write; roles you do not list are kept.
- Delete a whole `- id:` block to leave that album untouched.
- Mistakes (a `year` that is not a number, an unknown field...) reopen the editor with the
  reasons at the top; saving the text unchanged cancels.
- bdnex shows the changes and asks before storing them (`-y` skips the question).

`--set KEY=VALUE` changes one field of every matching album without the editor. Lists use `;`
(names contain commas): `--set genres='Humour; Fantasy' --set credits.Writer='Jarry, Nicolas; Co'`.

**Your edits are kept.** They are stored beside what the source said, and laid over it again
whenever the album is re-imported, re-cached or re-read with `bdnex reparse`; the edited
values are never reported as changes. Run `bdnex write series:nains` afterwards to put the new
values into the files.

### After an edit: rename the files

The folder and file names come from the album's metadata (`paths.series`, `paths.oneshot`, `paths.rules`), so a
changed title, series, volume or author means the files are no longer where the templates put them. Once the
library is placed, the whole round trip is:

```bash
bdnex edit series:nains --set title='New title' -y   # 1. change the library (or edit in $EDITOR)
bdnex move -n series:nains                           # 2. preview: each file shows `from` and `to`
bdnex move series:nains                              # 3. rename the files; the new ComicInfo.xml is written too
```

`move` works out the destination again for every file it is given. A file already in the right place says
`stays where it is`; the others are renamed or moved, the library follows, and the folders left empty are removed
(see [Folders left behind](writing.md)). Renaming a series with `--set series=...` moves all of its albums.
Use `bdnex write <query>` instead if you only want the tags updated and the names left alone.

## Paths, and moving your collection

The library stores the path of each file **relative to `directory`** (your comics folder in the
configuration) when the file is under it, and the full path otherwise. A comic imported from
somewhere else (`import.mode: in_place`) keeps working; it just keeps its absolute path.

Moving the whole collection is therefore one edit:

```bash
mv ~/comics /mnt/nas/comics
bdnex config --edit              # directory: /mnt/nas/comics
bdnex ls --missing               # nothing listed: every file was found
```

Nothing in the database is rewritten. If you point `directory` at the wrong place, the files
show up in `bdnex ls --missing` (and nothing is lost): fix `directory` and they are found again.

- **First start after upgrading.** The first command that runs with your `directory` set stores
  the paths under it relative to it, once. A library with relative paths cannot be opened
  without `directory`; bdnex says so and stops. An older bdnex refuses the database (schema v2).
- **Files outside `directory`.** Moved folders outside it, or paths you want to change one by
  one, are handled by `bdnex relocate`:

```bash
bdnex relocate -n /old/folder /new/folder        # preview: paths affected, how many found
bdnex relocate /old/folder /new/folder           # rewrite them (files and import log)
```

`relocate` only rewrites paths; it reads, hashes and touches nothing on disk. It refuses to
run if a new path is already registered, and only matches whole folders (`/comics` does not
rewrite `/comics2`). A path that lands under `directory` is stored relative.

`bdnex ls --files` and `path:` queries match the **stored** form, so `path:Series/Nains`
works for files in the tree, whatever `directory` is.

## Starting from scratch

```bash
bdnex reset -n         # show what would be deleted
bdnex reset            # asks, copies the database aside, then empties the library
```

`reset` forgets your collection: the files, albums, series, credits, genres, publishers, your
`bdnex edit` changes and the import history. It **keeps everything that was downloaded**: the
Bédéthèque sitemaps and every album / series page and cover already fetched. Importing again
therefore needs no network for them, and matching starts at once.

Your comic files are not touched, and neither is the `ComicInfo.xml` inside them. The [trash](versions.md#the-trash)
and its records are left alone too; `bdnex trash --empty` deletes them.

Before deleting, `reset` copies the whole database next to it as
`<library>.before-reset-<date>` (`--no-backup` skips this; the copy includes the downloaded data,
so it is as large as the library). To go back, replace the library file by that copy. `-y` skips
the question.

A typical fresh start with a library of any size:

```bash
bdnex reset
bdnex import --match --auto ~/comics        # seconds, no page requested
bdnex import --fetch                        # pages already stored are read offline; only new ones are fetched
```
