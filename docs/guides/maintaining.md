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

## Authors, and following a name

Each credited name is stored once (the `people` table), and the credits link an album, a person and a role. In the web page,
the names in an album's details are links: **a name** shows every album that person worked on, **as writer** (or penciller,
colorist...) only the albums where they have that role; a series, a publisher or a genre is a link too. The **People** page lists
everybody with how many albums they have and in which roles, with a search box. These are ordinary searches
(`bdnex ls 'author:=Moebius'`, `'writer:=Moebius'`, `'series:=Blueberry'`; `=` is an exact match, ignoring case and accents).
A name is unique by its spelling: the same person written two ways (`Moebius` and `Giraud, Jean`) is two entries, so correct a
spelling with the editor (the credits of an album, or **Edit these albums** on a search).

## The Files page

The Files page lists the files of a search (`format:pdf corrupted:true`...). With changes allowed, each row has an **Actions** menu:
open its album, write its tags, move it into place, repair it, **rematch** it (choose its album again), mark it as a spare
version, unlink it from its album, or move it to the trash (restorable from the Trash page). They are the commands of this
page's other sections on one file (`fileid:` in [queries](queries.md)). A file under a source that is only copied from is
never changed.

## Editing an album or a series

```bash
bdnex edit series:nains              # opens every matching album in $EDITOR (YAML)
bdnex edit 'Loup Noir'               # same query language as `bdnex ls`
bdnex edit series:nains --set publisher=Soleil --set year=2007 -y    # no editor
bdnex edit -n series:nains           # show the changes, store nothing
```

**In the web page.** Open an album and press **Edit**: a form with every field `bdnex edit` has (title, volume, publisher, date,
pages, language, format, ISBN, summary, genres, and the credits as one row per role). *Preview the changes* shows them like
`bdnex edit -n` and stores nothing; *Save* stores them as hand edits, kept across re-imports and `reparse`, with the same checks
(a month from 1 to 12, whole numbers, text where text is expected). Names such as `Jarry, Nicolas` are one name: separate several
with `;`. An emptied credit row removes the role, and renaming the series renames it for all its albums (the form says so).
On the **Albums** page, a search with results also offers **Edit these N albums**: the same as `bdnex edit QUERY --set ...`,
with a preview first. An edit changes the library only: **Write the tags into its files** (or `bdnex write`) puts the new values
into the files. Edits need `api.allow_changes` and the login, and wait while a task is running. Album numbers are `id:` in
[queries](queries.md).

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

The library stores each file's path in one of three forms:

| Where the file is | Stored as |
|---|---|
| under `collection_path` | the path **relative to it** |
| under an [import source](importing.md) | `@name/path below the source`, with the source's folder recorded once |
| anywhere else | the full path |

**Moving the whole collection** is one edit:

```bash
mv ~/comics /mnt/nas/comics
bdnex config --edit              # collection_path: /mnt/nas/comics
bdnex ls --missing               # nothing listed: every file was found
```

**Moving a source folder** is one command, and it does not touch your configuration:

```bash
mv ~/Downloads/comics /mnt/nas/new-downloads
bdnex relocate ~/Downloads/comics /mnt/nas/new-downloads   # updates the one record of that source
bdnex config --edit              # and the path of the source in import.sources, so the two agree
```

If the configuration and the library disagree about where a source is, bdnex warns at the next command and tells you
the `relocate` line to run. Nothing in the rest of the database is rewritten, and `relocate` reads and writes nothing
on disk.

- **`collection_path` never rewrites the database.** If it points at the wrong place, the files show up in
  `bdnex ls --missing` (and nothing is lost): fix `collection_path` and they are found again.
- **First start after upgrading.** The first command that runs with your `collection_path` set stores the paths under it
  relative to it, once. A library with relative paths cannot be opened without `collection_path`; bdnex says so and stops.
  An older bdnex refuses the database (schema v2).
- **Other files outside the collection** (full paths, not in a source): moved folders, or paths you want to change one
  by one, are handled by the same command:

```bash
bdnex relocate -n /old/folder /new/folder        # preview: paths affected, how many found
bdnex relocate /old/folder /new/folder           # rewrite them (files and import log)
```

For these, `relocate` rewrites the stored paths; it refuses to run if a new path is already registered, and only matches
whole folders (`/comics` does not rewrite `/comics2`). A path that lands under `collection_path` or a source is stored in
that form.

### Files moved or renamed by hand: `bdnex relink`

If you moved or renamed files outside bdnex (a file manager, another tool), they show in `bdnex ls --missing`.
`relink` looks for them by content:

```bash
bdnex relink -n ~/comics            # preview: which missing file was found where
bdnex relink ~/comics ~/Downloads   # link them again (default folder: `collection_path`)
```

A missing file is looked for by size and checksum in the folders you give (only files of a size a missing file had are
read, several at a time: `--workers N`, default `import.workers`). When exactly one copy is found, the
library points at it again, keeping its album, import history and edits. Several copies are listed and left alone,
and what is not found stays in `ls --missing`. Nothing on disk is touched.

`bdnex ls --files` and `path:` queries match the **stored** form, so `path:Series/Nains`
works for files in the tree, whatever `collection_path` is.

## Is the library healthy? `bdnex check`

```bash
bdnex check          # a report; changes nothing
bdnex check --fix    # also settles what is safe
```

`check` looks at the database itself (integrity, links between tables), at the files it points to (missing from disk;
changed in size since they were recorded; without an album; waiting for [`bdnex review`](importing.md)), at albums
with no file and series with no album, at cover files (an album pointing to one that is gone, files that belong to no
album), at the [trash](versions.md#the-trash) (records whose file is gone) and at temporary files left by an interrupted
write. Each finding says what to do about it. It exits with status 1 when it finds something, so it can run from a
script.

`--fix` only does what cannot lose anything, and marks it `[fixable]` in the report: remove empty series, forget cover
files that are gone (the next `bdnex import --fetch` downloads them), remove dead trash records, and delete the
temporary files. Files, albums and covers that exist are never touched.

## The same book twice: `bdnex duplicates`

```bash
bdnex duplicates            # identical files, and albums with several (non-spare) files
bdnex duplicates --covers   # also files whose first pages look alike (reads each file once, several at a time)
```

It only reports, in groups: *identical content* (same checksum at several paths, with the space they waste), *same
album* (several files linked to one album that are not marked as spare versions) and, with `--covers`, *similar
cover*. `--workers N` sets how many files are read at once with `--covers` (default `import.workers`; `1` is one by one);
the hashes are stored, so the next run only reads new files. Deciding is done by [`bdnex import`](versions.md), which compares the files and asks, and by
`bdnex trash` for what was replaced.

||||||| 9f8bd96

## Upgrades keep a copy

When a new bdnex has to change the structure of your library (the schema version), it first copies the database to
`<library>.bak-v<old version>` (for example `bdnex.sqlite.bak-v6`) and says so. An older bdnex cannot open the
upgraded file; to go back, put the copy in place of `bdnex.sqlite`. There is one copy per version, never overwritten,
and nothing is copied when the library is already current. Delete the copies when you no longer need them.

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
