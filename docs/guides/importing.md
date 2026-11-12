# Importing

`bdnex import <path>...` finds the Bédéthèque album of each comic file (`.cbz`, `.cbr`, `.pdf`),
reads the album page, and records everything in the library database. It does **not** modify
your files: writing tags and moving them is a separate step (see [Writing](writing.md)), unless
you ask for it with `-W`, `--move` or `--copy`.

```bash
bdnex init                       # once: download the album list the names are matched against
bdnex import ~/comics            # a folder, recursively
bdnex import album.cbz other.cbz
bdnex import -n ~/comics         # --pretend: show what would happen, change nothing
```

## Two steps

Importing is two steps, and you can run either alone:

| Step | Option | What it does | Network |
|---|---|---|---|
| **match** | `-m`, `--match` | finds the album of each file, from the album list | none |
| **fetch** | `-f`, `--fetch` | reads the album pages: authors, publisher, ISBN, summary, genres | yes, slow on purpose |

With neither option, **both** run. An album is *matched* once its file is linked to it (series, volume
and title are known), and *complete* once its page was read.

```bash
bdnex import ~/comics                 # match, then fetch
bdnex import --match --auto ~/comics  # a quick pass: link everything, request no page
bdnex import --fetch                  # later: read the pages of every matched album; Ctrl-C and resume
bdnex import --fetch ~/comics/Series/Nains    # only the files under a path
```

A step that is already done for a file is skipped, and the report says so. `--force` redoes the
steps you selected:

```bash
bdnex import --match --force file.cbz     # wrong album? ask again and relink the file
bdnex import --fetch --force ~/comics     # read the pages again, from the site
bdnex import --force file.cbz             # both
```

Running `bdnex import ~/comics` again is therefore cheap and does the natural thing: files already
done are only counted, new files are matched, and albums whose pages were not fetched yet get
them. A file you **skipped** is remembered and not asked about again, until you pass `--force` or
`bdnex init` has downloaded new albums.

## What happens, per file

1. **Done already?** A file linked to a complete album is reported `already` and left alone
   (`--force` to redo). One linked to a matched-only album gets its page fetched.
2. **Readable?** A corrupted archive is reported as `failed`; the rest of the batch continues.
3. **Identical file?** Same checksum as a file already in the library: handled by the
   duplicate action (below).
4. **Match.** The file name, and the folder name, are fuzzy-matched against the album list
   (no network). `T01`, `Tome 1`, `v3`… in the name are understood, a matching volume number
   raises the score and a different one caps it below *strong*. A second try uses the series and
   volume alone (`Loup Noir 5`): authors, years or a collection in the file name are not in the
   album's name. Those matches never count as strong.
5. **Decide.** By default **you check every file**: the best match is pre-selected, you
   confirm, pick another candidate or skip. With `--auto` (or `import.auto: true`) a score
   at or above `match.strong_threshold` is applied without asking; between
   `medium_threshold` and `strong_threshold` you choose among the candidates; below, you
   can still pick one or skip. `--auto=all` never asks: strong and medium matches are
   applied, the rest skipped. Without a terminal to ask on, the import stops once and says so.
6. **Same album, other file?** `duplicates.action` decides: `ask`, `skip`,
   `keep_both` or `replace` (the new file becomes the album's file; the old file stays
   on disk, unlinked).
7. **Linked.** The file is linked to the album as the album list tells it, so a decision is saved
   the moment you make it.
8. **Fetch.** Once every file is decided, the album pages are read one by one (politely slow,
   `bedetheque.delay`), with a progress bar and the time left. If a page cannot be fetched the
   album stays matched, with the URL to save. Ctrl-C stops here and loses nothing: run
   `bdnex import --fetch` to continue.

### Files from outside your library folder

You can import from any folder, such as `~/Downloads`. A file under `directory` is left where it is
(`bdnex move` can still tidy its name). A file **outside** it follows `import.outside`:

| `import.outside` | What happens |
|---|---|
| `ask` (default) | one question per run: move them, copy them, or leave them |
| `move` | tags are written, the file is renamed from your templates and moved into the library |
| `copy` | the same, and the original stays where it was |
| `leave` | nothing is touched |

`--move` and `--copy` apply to every imported file; `--no-move` leaves the outside ones where they are for
this run. Files that were left are **staged**: the library remembers the full path they came from, and
`bdnex move staged:true` places them whenever you like (`bdnex ls --files staged:true` lists them).
Without a terminal to ask on, `ask` leaves them. With `import.mode: copy|move` everything is placed and
nothing is asked. Before a file is moved in, it is compared with the files the album already has: see
[Duplicates, versions and the trash](versions.md).

### The album list: `bdnex init`

Matching works from the Bédéthèque sitemaps: about 55 files, 70 MB, downloaded once and kept in
the library. `bdnex init` downloads them with a progress bar; run it again whenever you want to
pick up new albums (it re-downloads all of them, about a minute, and tells you how many are new).
If you import before running it, bdnex offers to download the list on a terminal, and otherwise
stops and names the command.

### Matching never waits for you

Matching needs no network and no database, so it runs in background processes **ahead**
of the file you are reviewing (`import.lookahead` files), and the page fetching happens
after the review. Thinking about a file therefore never stops the matching, and the
politeness delay never stops you.

```yaml
import:
  auto: false       # true: apply matches >= match.strong_threshold without asking
  workers: 0        # processes matching in the background; 0 = CPU cores - 1, 1 = none
  lookahead: 100    # files matched ahead of the one you are reviewing
```

On 2,000 real file names against the full Bédéthèque index: 50 s with one process, 8 s
with 13. Workers are forked, so they share the loaded index; on a system without `fork`
(Windows) matching runs in the main process.

## The report

bdnex prints what it is doing on stderr (the match and its score, then a progress bar for the
pages). Each file ends with a line describing what was recorded:

```text
complete  100.0  /comics/La Bibliomule De Cordoue (Wilfrid Lupano) [2021].cbz
             -> La bibliomule de Cordoue (oneshot) · Lupano, Wilfrid; Chemineau, Léonard · Dargaud 2021 · 251 pages · Satire · complete
```

| Outcome | Meaning |
|---|---|
| `complete` | matched, and the album page was read |
| `matched` | matched, but the page was not read (not asked for, or not available) |
| `already` | done before; only listed with `-v`, always counted in the summary |
| `skipped` | no match, or skipped by you (now or earlier) |
| `duplicate` | an identical file, or another file of the same album, was handled by `duplicates.action` |
| `failed` | the archive could not be read |

Each run ends with a summary such as `96 file(s): 83 matched, 4 skipped, 9 duplicate`. `bdnex ls --long`
shows everything that was recorded. An argument that is not an existing file or folder is
reported as you typed it.

## Options

| Option | Effect |
|---|---|
| `-m`, `--match` / `-f`, `--fetch` | run only that step (default: both) |
| `--force` | redo the selected steps for files that are already done |
| `--auto` / `--auto=all` / `--no-auto` | apply strong matches without asking / never ask / check every file (overrides `import.auto`) |
| `-n`, `--pretend` | show what would happen; no database change, no page fetched, no question asked |
| `-W`, `--move`, `--copy` | afterwards write the tags / move into the library tree / copy there |
| `--no-move` | leave files from outside your library folder where they are (`import.outside`) |
| `--workers N` | background matching processes (overrides `import.workers`) |
| `-v` | list the `already` files too, and more detail |

The former names still work and say what replaced them: `--no-fetch` is `--match`,
`--retry-partial` is `--fetch`, `--recache` is `--fetch --force`. Note that `--force` no longer
means "never ask": that is `--auto=all`.

Defaults for thresholds and duplicate handling live in the
[configuration](configuration.md) (`match:`, `duplicates:`, `import:`).

## Re-reading what was stored: `bdnex reparse`

Every album page bdnex fetches is stored in the library exactly as received, so the library is the
permanent copy of what the source said. If bdnex later learns to read more of a page (or you want to
re-check a record), no page needs to be fetched again:

```bash
bdnex reparse -n            # preview: what would change, album by album
bdnex reparse               # update every album from its stored page
bdnex reparse series:nains  # only some albums (same terms as `bdnex ls`)
```

- It works **offline by construction**: the source is built with a fetcher that cannot reach the network
  (whatever `bedetheque.fetcher` says), and without the politeness delays.
- It never erases. Values the page gives override the library's; values it does not give stay, and so
  does a credit you typed at a prompt for a role the page does not list.
- Albums that are only matched (their page was never fetched) have no stored page: they are reported, and
  `bdnex import --fetch` is how to complete them.
- It changes the library, not your files: run `bdnex write` afterwards to put new details into them.
