# Importing

`bdnex import <path>...` finds the Bédéthèque album of each comic file (`.cbz`, `.cbr`, `.pdf`),
reads the album page, and records everything in the library database. It does **not** modify
your files: writing tags and moving them is a separate step (see [Writing](writing.md)), unless
you ask for it with `-W`, `--move` or `--copy`.

```bash
bdnex init                       # once: download the album list the names are matched against
bdnex import                     # your collection (`collection_path` in the configuration)
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
   **The file's own tags help.** A file that already has a `ComicInfo.xml` is read first. If its `Web` field is the
   address of a Bédéthèque album page (bdnex writes it, and so do other tools that took it from the site), that album
   is the first choice with a score of 100, whatever the file is called; a `Series` and a `Number` add a
   `series number` search like those made from the name. `match.use_embedded: false` turns this off. A wrong tag
   is not trusted blindly: the cover check still compares the covers.
5. **Decide.** By default **you check every file**: the best match is pre-selected, you
   confirm, pick another candidate or skip. With `--auto` (or `import.auto: true`) a score
   at or above `match.strong_threshold` is applied without asking; between
   `medium_threshold` and `strong_threshold` you choose among the candidates; below, you
   can still pick one or skip. `--auto=all` never asks: strong and medium matches are
   applied, the rest skipped. Without a terminal to ask on, the import stops once and says so.
   Each question starts with a rule, so the files are told apart, and names the file as `folder/name` (the folder
   it sits in, in cyan, then the file in bold; not the whole path). The scores are coloured on a gradient: **80 to 100
   green**, 60 yellow, 40 orange, **20 and below red**, so a doubtful match stands out at a glance. Colour is used on a
   terminal only: `NO_COLOR=1` turns it off, `FORCE_COLOR=1` forces it. The report at the end of the run uses the same
   colours for scores and outcomes.
   **Keys, as in beets.** Every line shows its key in brackets, highlighted: `[1]`..`[9]` pick a candidate,
   **E** enters a search, **U** types the address of the album's page, **S** skips the file, **F** skips the whole folder (the files of it still to come are not asked about; they are remembered as skipped, and `--force` looks at them again), **B** goes back to the previous file, **A** aborts the import.
   Upper or lower case, no Enter needed; the arrows and Enter work as usual. The same keys and colours are used by
   every question of every command (see [the keys](#the-keys-of-every-question)). **O** (or Ctrl-O) opens the page of
   the highlighted album in your browser.
   **Back** undoes what was decided for the previous file (its link in the library, a file moved to the trash or
   to `_versions/`) and asks about it again, so a wrong key press is not final; press it again to go further
   back. It does not cross the question *is this folder one series?*, which is asked once per folder, and which also offers to skip the folder.
   **A thumbnail of the file's first page** is drawn on the far right of the question when the terminal can show
   images (kitty, Ghostty, WezTerm, iTerm2) and is at least 100 columns wide, to help the choice. Inside tmux it is drawn when
   `set -g allow-passthrough on` is set and the terminal around it is Ghostty or kitty (tmux support is best effort).
   Over SSH the terminal cannot be detected: set `import.thumbnails` (`kitty`, `iterm2`, `never`) or the
   `BDNEX_THUMBNAILS` variable, which wins over the setting. Any failure drawing it is silent.
   **The right album is not in the list?** Choose *Enter a search in the album list...* (the album you pick there is
   shown again for you to check, with **O** to open its page, before it is used; **N** returns to the list), or
   *Type the album's URL...* and paste the address of its page (`https://www.bedetheque.com/BD-...-<number>.html`).
   The search: with [`fzf`](https://github.com/junegunn/fzf) installed, it opens over the whole album list (about 370,000),
   started from the file's name and case-insensitive; otherwise you type a few words and filter their best
   2,000 hits in a Python fuzzy prompt. Esc / Ctrl-C goes back to the list. **Ctrl-O** opens the page of the
   highlighted album in your browser, in the list and in both searches, to check before you choose.
   **Abort the import** (last line of the question) stops the run on the spot. Every decision made so far is
   already saved, the file you were looking at stays undecided (it is asked about again, unlike a skipped one),
   no album page is fetched, and files imported from outside your library folder are left where they are and
   noted as *staged*. The library remembers where you stopped: run the same command again and bdnex says
   `Resuming: the last import was aborted at ...`, does not ask about the decided files again, and goes on.
6. **Same album, other file?** `duplicates.action` decides: `ask`, `skip`,
   `keep_both` or `replace` (the new file becomes the album's file; the old file stays
   on disk, unlinked).
7. **Linked.** The file is linked to the album as the album list tells it, so a decision is saved
   the moment you make it.
8. **Fetch.** Once every file is decided, the album pages are read one by one (politely slow,
   `bedetheque.delay`), with a progress bar and the time left. If a page cannot be fetched the
   album stays matched, with the URL to save. Ctrl-C stops here and loses nothing: run
   `bdnex import --fetch` to continue.

### Folders with several files

A folder with several comics is, most of the time, a series whose files are its volumes, and bdnex matches
the folder as a whole (as beets does with an album folder). It looks at the file names:

| The folder holds | What bdnex does |
|---|---|
| files that each have **their own volume number** and start with the same title (`Aâma - T01 - ...`, `T02`, `T03`) | takes it for a series **without asking** |
| the **same book** in several files (`X.cbr` and `X.pdf`, or the same volume twice with a near-identical name) | not a series: these are versions, handled by the [duplicate question](versions.md) |
| anything else (some files without a volume, unrelated titles...) | **asks**, listing the files: *Is this one series?* |

**Titles made of numbers** (`14-18`, `666`): a name that opens with two or more numbers is read as the title's numbers
followed by the volume (`14-18 - T02` is the series *14-18*, volume 2, not volumes 14, 18 and 2). Such a series has no
word to look it up by, so bdnex looks for albums titled with exactly those numbers, then checks the volume. Four-digit
titles (`1984`, `2021`) are years in a file name and matched by their words as usual.

For a series, the files **vote**: each file votes for the series that has an album with its volume among its own best
matches, and the series with most votes wins when at least half of the files agree. Every file is then given
the album of that series with its volume number, which also pulls in files whose own best match was another
series (`100 Bullets` T01 matching `Stray Bullets`). A series that shares no word with the folder or file names is
never suggested. Files with no volume (specials) are matched one by one as usual, and answering *No* matches every
file by itself.

**You picked an album by hand.** When a folder was not taken for a series (its files did not agree on one), but you pick
an album of a series for one of its files, bdnex offers that series for the **other files of the folder**: it looks for
the album of the same series with each file's volume number, and asks once, with the same answers as *Is this one
series?* (yes, no, choose which files, skip the folder). **Back** takes the offer back together with your choice. It is
not offered after `--no-series`, `--auto=all`, or for a one-shot.

**A folder that holds two series** (say `Achille Talon` volumes plus a sub-series, or a few unrelated books): at
*Is this one series?* choose *Choose which files belong to it*. A list of the folder's files opens; the ones the
series was found in, and that are named like most of them, start ticked, **Space** ticks or unticks, **Enter** confirms. The ticked files become that
series. The others are looked at as the next series (the same question again, for them only), and so on until
nothing is left; at any step you can say *No* (each file by itself) or *Skip this folder*. Ticking nothing matches
each file by itself.

`--series` (or `import.series: always`) assumes a series for every multi-file folder without asking;
`--no-series` (`import.series: never`) matches every file alone. A preview, `--auto=all` and a run without a
terminal never ask: they match file by file.

### Thumbnails when bdnex runs on a server (SSH)

The picture is drawn by **your local terminal**, not by the server. bdnex sends the image through the SSH
connection as an escape sequence (a few tens of KB per file), so it works when bdnex runs on a remote machine, as
long as the terminal you type in can show pictures: Ghostty, kitty, WezTerm or iTerm2.

The only catch is detection. bdnex guesses the terminal from environment variables (`TERM_PROGRAM`,
`KITTY_WINDOW_ID`...) that SSH does not carry to the server, so tell it once, on the server:

```bash
# ~/.config/bdnex/bdnex.yaml on the server
import:
  thumbnails: kitty        # Ghostty, kitty, WezTerm: kitty    iTerm2: iterm2

# or, for one session (it wins over the setting)
export BDNEX_THUMBNAILS=kitty
```

- If you run bdnex inside **tmux on the server**, add `set -g allow-passthrough on` to the server's `tmux.conf`
  (tmux 3.3 or later), as for a local tmux. If your local terminal also runs tmux, it needs the same line.
- **GNU screen** and **mosh** do not pass the picture on: no thumbnail, nothing breaks.
- The terminal must be at least 100 columns wide; a narrower one shows no picture.
- The picture is made on the server from the file's first page, so bdnex needs PyMuPDF there (installed with it) and
  the archive tools for CBR (`unrar`).
- To check that it works, run the diagnostic of the repository
  (`python tools/thumbcheck.py some/file.cbz`) or import a small folder and look at the first question (a preview,
  `-n`, never asks, so it shows nothing). When nothing appears, no
  character is printed either: a failing picture never stops an import.

### Your collection, and where new files come from

bdnex keeps one **collection**: the folder `collection_path` in the configuration, which it organises with your
`paths` templates. Comics reach it in two ways.

**1. A collection you already have.** Point `collection_path` at it, then:

```bash
bdnex import --as-is              # record every file where it is (nothing is changed)
bdnex import --select tagged:false   # match and fetch them
bdnex move                        # reorganise the folders from the templates (-n to preview, `bdnex undo` to go back)
```

**2. Folders new files arrive in** (a download folder, a NAS inbox): list them as **sources**.

```yaml
collection_path: ~/Comics
import:
  sources:
    - {path: ~/Downloads/comics, mode: move}              # emptied as the files are placed
    - {path: /mnt/nas/inbox, mode: copy, name: nas}       # read-only: the originals are never touched
```

```bash
bdnex import --sources        # what is new in every source: matched, tagged, then copied or moved into the collection
bdnex import .                # the current folder: a source (or inside one) follows its mode, with no question
bdnex import ~/some/folder    # any other folder: see "other folders" below
```

- **Where files end up:** in the collection, in the folder your templates give, with their tags written. The library then
  tracks the copy (or the moved file), under the collection root, with a relative path.
- **`mode: move`** moves the files and removes the source folders it empties. **`mode: copy`** leaves the originals
  exactly as they were: bdnex never writes, converts, moves or deletes a file under a `copy` source (`write`, `repair`
  and a plain `move` refuse it and say why; `bdnex move --copy` copies it in).
- **Importing a source twice is safe.** Each copy remembers the file it came from (path, size, date). The next
  `import --sources` reports `already copied to …` and does nothing; a file that was **changed at the source** after it
  was copied is reported and left alone (your edited copy is not overwritten), and `--force` imports it again.
- **Where the library keeps a source.** A file recorded in place under a source (`--as-is`, or before it is placed) is
  stored as `@name/path below the source`, and the source's real folder is in the library once. If the folder moves,
  `bdnex relocate OLD NEW` updates that one record (see [Paths](maintaining.md)); give a source a `name` so it keeps its
  identity if you rename the folder.
- **Flags win over the configuration for one run:** `--move` and `--copy` apply to every imported file, `--no-move`
  leaves the files from sources and other folders where they are.

**Other folders** (not in `import.sources`, outside the collection) follow `import.outside`:

| `import.outside` | What happens |
|---|---|
| `ask` (default) | one question per run: move them, copy them, or leave them |
| `move` | tags are written, the file is renamed from your templates and moved into the collection |
| `copy` | the same, and the original stays where it was |
| `leave` | nothing is touched |

Files that were left are **staged**: the library remembers the full path they came from, and `bdnex move staged:true`
places them whenever you like (`bdnex ls --files staged:true` lists them). Without a terminal to ask on, `ask` leaves
them. With `import.mode: copy|move` everything is placed and nothing is asked. Before a file is moved in, it is
compared with the files the album already has: see [Duplicates, versions and the trash](versions.md).

### Covers as files

When an album's page is read, its cover is also downloaded (one more polite request) and kept as a real image file
in `cover.dir` (`~/.local/share/bdnex/covers`), in a folder of its own: `<source>/<album id>/cover.jpg`. The album id is
unique within a source, so the name is too, and the folder has room for more pictures of the album later. Covers saved by
earlier versions as `<source>/<album id>.jpg` are moved into their folders by the next import (or `bdnex check --fix`). The library links it from the album
(`albums.cover_file`, the path below `cover.dir`) next to the address it came from (`cover_url`), so
[`bdnex serve`](api.md) can show it and other tools can use it. `cover.download: false` turns this off.

Covers are downloaded with a plain web request, not with the page fetcher of your configuration (a browser-based one
returns the little HTML page that wraps an image, a few hundred bytes, not the picture). What comes back is checked:
anything that is not an image of at least 100 pixels (an error page, a placeholder) is **not** saved, and the report
says what was received (`cover not downloaded: 446 bytes were received, which is not an image (it starts with ...)`).
A bad file left by an earlier run is deleted and downloaded again the next time. Each cover is downloaded right after
its album page is read; the albums that are complete but still lack a cover are done in a second pass at the end, and
Ctrl-C stops both.

A cover that is not there yet is downloaded the next time the album goes through `bdnex import --fetch`, which
covers albums imported before this existed (`bdnex import --fetch` on your library). A deleted cover file is
downloaded again the same way, and a file kept after `bdnex reset` is taken up again without any request.

### Checking covers

With OpenCV installed (`pip install 'bdnex[cover]'`, or `uv tool install 'bdnex[cover]'`), the front cover of each
file is compared with the album's cover (SIFT features, which cope with different sizes, crops and compression)
after its page is read. That is `cover.check: auto`; `true` insists on it (and says when OpenCV is missing),
`false` turns it off, and `--cover-check` / `--no-cover-check` decide for one run. The comparison uses the saved
cover file, so it costs no request of its own. The similarity (0-100) is kept with the file.

Below `cover.match_percentage` (40 by default) the match is doubtful:

- on a terminal, with questions allowed, you are asked at once whether to keep the match or unlink it;
- in a run that does not ask (`--auto=all`, no terminal), the match is kept and the file is **flagged for review**.

Go through the flagged files later with [`bdnex review`](#reviewing-doubtful-matches-bdnex-review). A file whose album
is already complete gets its cover compared the first time it goes through `bdnex import --fetch`.
`bdnex ls --files cover:..40` lists the doubtful ones by score, `review:true` the flagged ones.

### Reviewing doubtful matches: `bdnex review`

```bash
bdnex import --auto=all ~/comics     # match, read the pages, save and compare the covers: doubtful ones are flagged
bdnex review                         # go through the flagged files, one by one
bdnex review series:nains            # only some of them (same terms as `bdnex ls --files`)
bdnex review --list                  # just list them, and why
```

Each flagged file is shown with the file's cover on the left and the album's cover on the right (in one picture, when
the terminal can show pictures; see the thumbnails above), and you choose:

| Key | Does |
|---|---|
| **K** | keep the match: it is right, the flag goes |
| **U** | unlink the file from this album (it is remembered as skipped) |
| **R** | match it again: after the list, the usual match question is asked for these files |
| **S** | skip: it stays flagged for another time |
| **A** | abort the review; what was decided is kept |

A flag also goes when the file is linked to another album. `bdnex import` ends with a line telling how many files wait
for a review.

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

## Recording files first, matching later: `--as-is`

```bash
bdnex import --as-is ~/comics     # record every file, nothing else
bdnex ls --files tagged:false     # they are in the library, not yet linked to an album
bdnex import ~/comics             # later: match and fetch them, as usual
```

`--as-is` reads each file once and stores its path, format, size, page count, checksum, whether it looks damaged,
and what its own `ComicInfo.xml` says (series, number, title... kept as the file's own `embedded` data, apart from
any album). It asks nothing, uses no network, and moves, converts or writes nothing. The files are read in several processes at
once (`import.workers`, or `--workers N`): on a big collection over a slow disk or a network share that is the part that
takes the time. Files already in the library are
left alone (`--force` records them again). `ls --files -l` shows the file's own metadata, and `embedded:` finds files
by it (`bdnex ls --files embedded:nains`). Recorded files are not "skipped": the next `bdnex import` matches them.

### Filling the library without touching a file: `--database-only`

`bdnex import --database-only ~/comics` matches, reads the album pages and records everything in the library, and
**changes no comic file**: nothing is moved, copied, tagged, converted or deleted, whatever `import.mode`,
`import.outside`, `import.sources` or `duplicates.action` say. A file that is a duplicate of one already recorded
is skipped (reported as a duplicate), never replaced. What bdnex does write is its own: the library, and the cover
folder (`cover.dir`). It can not be combined with `-W`, `--move` or `--copy`. The difference with `--as-is`: that one
records files without matching or network; `--database-only` does the whole match and fetch.

### Watching the sources: `watch.enabled`

With `bdnex serve` (or the [Docker](docker.md) image) running, `watch.enabled: true` makes the server look at your `import.sources`
every `watch.interval` minutes and run `bdnex import --sources --unattended` for what is new: the confident matches are applied, each
file follows its source's mode (copy or move into the collection), and **nothing is asked**: a file whose match is not
confident, a duplicate, a name clash or a tag conflict is not decided for you (a doubtful one stays skipped, a clash gets a number,
the database wins a conflict, originals are kept). Whatever is left is yours to settle whenever you like, with the **import** task
on the Tasks page ("only the files skipped earlier") or `bdnex import --skipped`. A look is skipped while another task runs, each one
is a task in the list of runs ("Watch the import sources"), and the setting can be changed from the Settings page without a restart.
It needs a configured source and the album list (`bdnex init`).

### Asking again about skipped files: `--skipped`

A file you (or the matching) skipped is remembered, and a normal `bdnex import` leaves it alone ("skipped earlier") until
`bdnex init` brings new albums. To look at just those files again, whatever the albums list says:

```bash
bdnex ls --unmatched          # what was skipped (or failed)
bdnex import --skipped        # ask about those files again; `--skipped-earlier` is the same
```

Only the files skipped at an earlier import (and still on disk) are matched, so a library of thousands is not gone
through again. The report of a normal import ends with the number of skipped files and this command.

### Importing files that are already in the library: `--select`

`bdnex import <folder>` works on the files of a folder. To work on files the library already knows, select them with
the [query language](queries.md) of `bdnex ls --files`:

```bash
bdnex import --select tagged:false                    # match + fetch everything recorded with --as-is
bdnex import --select tagged:false --select 'embedded:nains'   # terms are ANDed
bdnex import --fetch --force --select author:uderzo   # read the pages of an author's albums again
bdnex import --match --force --select 'series:/^(antares|centaure)$/'   # ask again about these albums
bdnex import -n --select format:pdf                   # a preview of what would be done
```

`--select` can be repeated and combined with paths; the steps and options (`--match`, `--fetch`, `--force`,
`--auto`, `--move`...) are the same as for a folder. A bare `bdnex import` looks at your library folder only, so files
recorded from elsewhere need `--select` (or their folder).

## The report

A long import is shown in steps, each under its own heading: *Step 1/2  Matching N file(s) to albums*, then *Step 2/2  Reading N
album page(s)* (with a progress bar), then *Result* and, when files are placed, *Files*. Progress lines are coloured on a
terminal (counter in cyan, file names in bold, matches in green, problems in red); `NO_COLOR=1` turns it off.

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
| `--series` / `--no-series` | assume every folder with several files is a series without asking / never group files by folder (overrides `import.series`) |
| `--database-only` | only fill the library: no comic file is moved, copied, written, converted, replaced or deleted, whatever the configuration says (a duplicate is skipped). Not with `-W`, `--move`, `--copy` |
| `--unattended` | ask nothing: confident matches are applied, the others stay skipped for you (`--skipped` asks about them later); no question about duplicates, name clashes or conflicts. What the folder watch of `bdnex serve` runs |
| `--no-move` | leave files from outside your library folder where they are (`import.outside`) |
| `--cover-check` / `--no-cover-check` | compare each cover with the album's (overrides `cover.check`) |
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


## The keys of every question

All questions of bdnex (import, review, write, move, repair) take their keys and colours from one table, so
the same answer is the same key everywhere. Press the highlighted letter, in either case, or use the arrows and Enter.

| Key | Answer | Colour |
| --- | --- | --- |
| `S` | skip this file (or this folder, in the series question) | yellow |
| `F` | skip the whole folder (match question) | yellow |
| `B` | back to the previous file | blue |
| `A` | abort: what is decided is kept, run it again to continue | red, always last |
| `Y` / `N` | yes / no | green / yellow |
| `K` | keep the match (review), or keep the file as it is (conflicts) | green / red |
| `L` | unlink the file from the album (review); leave it as it is (damaged archive, outside files) | yellow |
| `D` | the database wins (conflicts) | green |
| `W` | the database wins for the rest of the queue (conflicts) | green |
| `E` / `U` | enter a search / type the address of the album | cyan |
| `R`, `C`, `M` | rematch, choose (files, fields), move | cyan |

A key is the same in every question where its answer exists, and a question never uses one key twice. Green is the safe
answer, yellow puts it off, red is destructive or stops the run. `NO_COLOR=1` turns the colours off, the keys stay.
