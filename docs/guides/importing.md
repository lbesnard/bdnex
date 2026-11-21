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
   **The file's own tags help.** A file that already has a `ComicInfo.xml` (inside a CBZ or CBR, or attached to a PDF, where `write.pdf: tag` puts it) is read first. If its `Web` field is the
   address of a Bédéthèque album page (bdnex writes it, and so do other tools that took it from the site), that album
   is the first choice with a score of 100, whatever the file is called, **provided it is in the album list** (an
   address that is not there, such as a stale or wrong one, or an album newer than your list, is not trusted: the name
   is used instead, and `bdnex init` refreshes the list; `-vv` says when an address was not trusted); a `Series` and a `Number` add a
   `series number` search like those made from the name. `match.use_embedded: false` turns this off. So a collection that is already tagged with bdnex, or by a tool that wrote the
   Bédéthèque address, matches without a guess from the file name. A wrong tag
   is not trusted blindly: the cover check still compares the covers.
   **What the library already knows helps.** Among the candidates whose album page was read, a year in the file name (or
   tags) within a year of the album's earns 2 points and an author in a `(group)` of the name that is among the credits
   earns 3. Nothing is ever taken away (an album whose page was never read has no year to compare, so a penalty would
   push every known album behind the unknown ones; measured on a real library it moved right matches to wrong ones).
   The albums read *during* a run are not in it until the next run.
   **Where a score comes from.** The score (0-100) is the *title score* (how many words of the name the album has, and how
   alike the texts are, with a floor when every word of the album's name is in the file name), then adjusted: the volume
   (+5 when the file's volume is the album's, -15 and at most 85 when it is another), the year (+2), an author (+3), and, for a file that
   says *intégrale* and gives no volume, +8 for an album that is a collected edition (its address says `INT`) and -8 for a single tome of the series.
   `bdnex candidates` prints each candidate's breakdown on `=` lines. The four adjustments are `match.volume_bonus`, `volume_penalty`, `year_bonus` and
   `author_bonus` in the configuration, so you can make the volume count more or less; `tools/eval_matching.py` tells you what a change does to
   the wrong matches before you keep it.
   **A strong score is not always enough.** A first choice is not applied unasked, even above `match.strong_threshold`,
   when **another album is fewer than `match.min_margin` points behind it** (10 by default; a tie between tome 1 and
   tome 2 of a series is the usual case), or when the file's own tags name another series, or the file is a supplement
   or an "intégrale" and the album a numbered tome. It is then asked about (or, with `--auto=all`, left undecided). `bdnex import -v` says which. `match.min_margin: 0` turns the margin off. A file that names its album by its
   address is never doubted.

   *How reliable is it?* Measured on a library of 16,343 linked files, using the 3,588 whose album was confirmed by something
   other than the name (the year, an author, the file's own tags) as the answer key: a first choice scoring 90 or more
   was wrong 11 times in 2,876 (0.4%); with the margin, 2 times in 2,632 (0.08%), and 244 files that were right 96% of
   the time were asked about instead. Between 85 and 89 it was wrong 4.4% of the time, which is why `--auto` asks
   about those and why a first pass with `--auto=all` is best followed by a checked one (see the cookbook).
   `tools/eval_matching.py` makes the same measurement on your own library.
5. **Decide.** By default **you check every file**: the best match is pre-selected, you
   confirm, pick another candidate or skip. With `--auto` (or `import.auto: true`) a score
   at or above `match.strong_threshold` is applied without asking; between
   `medium_threshold` and `strong_threshold` you choose among the candidates; below, you
   can still pick one or skip. `--auto=all` never asks: only strong matches without a doubt are applied (`match.auto_all: medium` applies the
   medium ones too, as it did before), and the rest is skipped, then given to the cover vote (see below). Without a terminal to ask on, the import stops once and says so.
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
   *Enter an album Id or address...* (**I**): type the album's number (the one at the end of its page address,
   `...-245127.html` is `245127`) or paste the address of its page. The album is shown for you to check before it is used.
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
8. **Fetch.** The album page of a linked file is requested **at once, in the background**, while the next
   files are still being matched and asked about, and so is its series page (once per series). Every request
   waits `bedetheque.delay`, and no more than `bedetheque.parallel` run at a time. As soon as an album's pages are
   here, **the album's record is built in the library** (authors, summary, date, series): `bdnex ls complete:true -c`
   grows while the import runs, and the progress line says how many albums are complete. Nothing waits for the
   network: the building reads the stored pages. The covers are downloaded in the background too, as each album becomes complete. When the last file is
   decided, the pages still on their way are waited for, with a progress bar. If a page cannot be fetched the
   album stays matched, with the URL to save. Ctrl-C stops everything: the albums already complete (and those whose
   pages are stored) are kept, and `bdnex import --fetch` continues with the rest. Nothing is requested with `-n`;
   `-v` says each page queued and each album completed, `-vv` logs each request (`grep -i fetch`). Only the
   library changes: with `--database-only` no comic file is touched either way.

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
- To check that it works, import a small folder and look at the first question (a preview,
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
A bad file left by an earlier run is deleted and downloaded again the next time. Each cover is downloaded in the background (two
at a time) as soon as its album is complete, and compared with the file's cover when it is here (a cover that differs
is flagged for `bdnex review` rather than asked about while files are still being matched). Albums that were already
complete but lack a cover are done at the end. Ctrl-C stops all of it; `bdnex import --fetch` resumes.

A cover that is not there yet is downloaded the next time the album goes through `bdnex import --fetch`, which
covers albums imported before this existed (`bdnex import --fetch` on your library). A deleted cover file is
downloaded again the same way, and a file kept after `bdnex reset` is taken up again without any request.

### The cover vote: deciding what the title could not

With `--auto=all` nobody can be asked, so a match in the middle (a score between `medium_threshold` and
`strong_threshold`, or a strong one with another album close behind) is **not applied**: a wrong link costs more than
a missing one. At the end of the run those files get a second chance, at low priority (after everything else, and
Ctrl-C stops it): the covers of their three best candidates are compared with the file's cover (a candidate whose cover
was already saved costs no request; any other is one plain request, three at a time). A candidate wins when its cover
looks like the file's by at least `match.cover_vote_min` (60%) **and** leads the next one by 15 points; the file is then
linked as `(cover vote 78%)` and its page is read like any other. Otherwise it stays skipped, and `bdnex import --skipped`
(or `bdnex candidates FILE --cover`) comes back to it. `match.cover_vote: false` turns the vote off; it needs OpenCV.
`match.auto_all: medium` brings back the old behaviour (medium matches applied, nothing voted).

### Checking covers

With OpenCV (installed with bdnex), the front cover of each
file is compared with the album's cover (SIFT features, which cope with different sizes, crops and compression)
after its page is read. That is `cover.check: auto`; `true` insists on it (and says when OpenCV is missing),
`false` turns it off, and `--cover-check` / `--no-cover-check` decide for one run. The comparison uses the saved
cover file, so it costs no request of its own. The similarity (0-100) is kept with the file.

Below `cover.match_percentage` (3 by default) the match is doubtful. (The similarity is the share of distinctive features of one cover found in the other: measured on a real
library a right match scores about 50, not 100, an album of another series about 0, and 3 flags 5 % of the right matches while catching all the wrong-series ones and
about 43 % of the wrong-volume ones. See `tools/eval_covers.py`.)

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
bdnex check --matches                # weigh every match against its album page and list the ones that look wrong
bdnex check --matches --fix          # the same, and flag them; then `bdnex review` goes through them
bdnex review --list --json           # the flagged files with the reason, for a script or an assistant
```

### Saying which album it is: `import --album`

```bash
bdnex import --album 'https://www.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html' ~/comics/nains1.cbz
bdnex import --album 245127 ~/comics/nains1.cbz       # the number at the end of the address, if the album is in the album list
```

For **one file**, uses that album: nothing is searched or asked, and a file that already has an album is redone (the
page is read again). It is the way to apply what `bdnex candidates` found, from a script or by hand. An address of any
album page works, even a very recent one; a number must be in the album list (`bdnex init` refreshes it).

### The other albums a file could be: `bdnex candidates`

```bash
bdnex candidates --low                       # the files whose match is in doubt, each with its best albums
bdnex candidates series:nains --limit 8      # chosen files, whatever their state
bdnex candidates --low --json                # for a script or an assistant
bdnex candidates --low --limit 3 --cover     # also compare the covers (saved ones are read from disk)
bdnex candidates --low series:nains          # a query narrows it: the same terms as `bdnex ls --files`
```

`--low` selects the files with **no album**, **flagged** for review, whose match **looks doubtful or contradicted**
(see below) or that were matched with a score under `match.strong_threshold`. For each file it lists the best albums
of the album list (`*` is the one now linked) with their score and the evidence for and against: the file's own tags,
volume, year and authors. Only an album whose page was read has a year and credits to weigh; the others say
`page not read`, so a candidate that looks right is worth a `bdnex import --fetch` before you decide. Nothing is
requested and nothing changes (with `--cover`, one cover per candidate is requested, politely). To redo a file:
`bdnex import --match --force FILE`. See [Checking the matches](checking-matches.md) for the whole procedure, by hand or with an assistant.

`--cover` compares the file's cover with each candidate's (needs OpenCV, see [covers](#checking-covers)) and marks the
candidate most like it. An album whose page was not read has no known cover address: the usual one, built from the
album's number, is tried, and a candidate whose cover can not be got is simply not compared.

#### Weighing a match: `check --matches`

The first match only has names. Once the album page is read, `bdnex check --matches` (and every import, as each page
arrives) compares the file with what the page says, offline and without reading any file:

| Signal | Agrees when | Against |
|---|---|---|
| the file's own tags | their series or title is the album's (or inside it) | they name another book: **strong** |
| the volume | the file's volume is the album's | another volume, or a "supplément", "hors-série" or "intégrale" matched to a numbered tome: **strong**, unless the album's title is written in the file name (then it is probably another numbering, and only noted) |
| the year | a year in the file name or tags is within a year of the album's | another year: weak (a reissue has one) |
| the authors | a name in a `(group)` of the file name is among the credits | none of them is: weak (a group is often a publisher or a scanner) |
| the folder | (`check --matches` only) the album is of the series most files of the folder are of | a folder of four files or more, mostly one series, and this album is of another series sharing no word with it: weak |

The verdict is `confirmed` (the year, an author or the file's tags agree), `unverified` (nothing independent to
compare: the volume alone confirms nothing, since the match was ranked on it), `doubtful` (a strong sign against but
something agrees, or two weak ones and nothing for) or `contradicted` (a strong sign against and nothing for). The
last two are flagged, with the reason, and **left linked**: nothing is unlinked without you. Most files are
`unverified`: a file name with no author or year gives nothing to weigh.

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

### Keeping the whole catalogue: `bdnex mirror`

An import only reads the pages of the albums your files match, so the library holds a part of the site. `bdnex mirror` fills the rest: it
compares the album list (`bdnex init`) with the library and fetches every album that is missing (or only matched), with its series
page and its cover (saved in `cover.dir`). It uses the same endpoints and the same polite delay as an import, several pages at once
(`bedetheque.fetcher_endpoint`, `bedetheque.parallel`, `--workers`).

```bash
bdnex mirror -n                  # how many are missing, and about how much room they take; fetches nothing
bdnex mirror --limit 500         # the next 500 albums, then stop
bdnex mirror                     # everything that is left (Ctrl-C stops; run it again to go on)
bdnex mirror --covers-only       # only the covers of the albums already here that have none
bdnex mirror --no-covers         # the pages only
```

The library is the state, so there is nothing to resume: a run that stops (Ctrl-C, or 10 failures in a row, which means the site is
probably blocking) leaves what arrived, and the next run goes on with what is missing. Pages are stored compressed (about a tenth of their
size: a whole catalogue is some GB, not tens). Covers are saved as files next to your own. Albums fetched this way are in the library
like any other: `bdnex ls`, `series` and `candidates` see them, and a file you import later finds its page already stored.

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
  workers: 0        # processes that match names and read each file ahead (check + checksum, once); 0 = CPU cores - 1, 1 = none
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

### An album that already has a file: `--keep-both`

`bdnex import --album ADDRESS FILE --keep-both` links the file to the album even when another file already holds it; the new one
becomes a spare version (see `bdnex duplicates`). Only the library changes, so it also works with `--database-only`.

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
| `--workers N` | processes that match names and read each file ahead (overrides `import.workers`) |
| `-v` | list the `already` files too, and more detail |

Note that `--force` does not mean "never ask": that is `--auto=all`.

Defaults for thresholds and duplicate handling live in the
[configuration](configuration.md) (`match:`, `duplicates:`, `import:`).

## Re-reading what was stored: `bdnex import --reparse`

(This was the command `bdnex reparse`, which still works, with a note.)

Every album page bdnex fetches is stored in the library exactly as received, so the library is the
permanent copy of what the source said. If bdnex later learns to read more of a page (or you want to
re-check a record), no page needs to be fetched again:

```bash
bdnex import --reparse -n            # preview: what would change, album by album
bdnex import --reparse               # update every album from its stored page
bdnex import --reparse --select series:nains  # only some albums (same terms as `bdnex ls`)
```

- It works **offline by construction**: the source is built with a fetcher that cannot reach the network
  (whatever `bedetheque.fetcher` says), and without the politeness delays.
- It never erases. Values the page gives override the library's; values it does not give stay, and so
  does a credit you typed at a prompt for a role the page does not list.
- Albums that are only matched (their page was never fetched) have no stored page: they are reported, and
  `bdnex import --fetch` is how to complete them.
- It changes the library, not your files: run `bdnex write` afterwards to put new details into them.
- Parsing the pages is the slow part, so it is done in several processes (`--workers N`, default one fewer than the cores), one series to a task,
  and the library is written afterwards. A whole library of 16,000 albums takes a few minutes.


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
| `E` / `I` | enter a search / enter an album Id or address | cyan |
| `R`, `C`, `M` | rematch, choose (files, fields), move | cyan |

A key is the same in every question where its answer exists, and a question never uses one key twice. Green is the safe
answer, yellow puts it off, red is destructive or stops the run. `NO_COLOR=1` turns the colours off, the keys stay.

## What the import tells you: `-v`, `-vv`, `-vvv`, `--log-file`

A big collection must neither scroll for hours nor hide why a file was left alone. Four levels, on every command:

| Option | What you see |
|---|---|
| (none) | the phases, a line every percent or so (`[1200/18743] 6%  about 2 h left`), and **only the files that need a look**: `skipped: name - no match: best guess BD-Berserk-Tome-36-... scored 63, under 70, the lowest score applied`, `duplicate: ... - identical to ...`, `failed: ...` |
| `-v` | every file, and under it what was decided and why: the album and its match score as a percentage, the page fetched, the reason a file was skipped; a `cover: 87%  file name` line when a file's cover was compared with its album's (it comes a little after the match: the pages and covers are downloaded in the background). On a terminal both numbers are coloured, red (bad) to green (good) |
| `-vv` | how: what each worker read, cache hits and misses, requests and their time, with the thread's name on each line (bdnex's own messages only) |
| `-vvv` | everything, for debugging bdnex itself: the debug messages of every library too, and each SQL statement. Very long |
| `--log-file PATH` | in addition, writes everything bdnex logs at the debug level, with the time, to this file, whatever the level on the screen: run a long import with `--log-file import.log`, keep the screen quiet, and send the file when something looks wrong |

The reasons are also kept: each skipped file is recorded with the score of its best guess (`bdnex import --skipped` asks about them again).
