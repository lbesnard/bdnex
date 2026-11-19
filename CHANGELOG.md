# Changelog

bdnex is pre-1.0: commands, options and the library schema change, sometimes in ways that break. **Breaking** changes
are marked. Back up your library before upgrading (copy the library file while no bdnex
command runs, never while an import is fetching).

## Unreleased

### Added
- Path templates have **`$special`**: what the site calls the tome when it is no number (`INT` for an intégrale, `HS`...), empty otherwise:
  `$series - %if{$special,$special,T$volume:02} - $title`.
- `review --batch`: **`space` marks files, `a` attaches them to a series**: pick any album of the series and each marked file (or the files of the
  folder) gets the album with the volume number of its name, after one question.
- `review --batch`: **`c` shows the covers**: the file's first page and the album's saved cover side by side in a popup, drawn with coloured text
  (so it works over SSH). Nothing is fetched.
- `review --batch`: linking a file to an album that **already has a file** no longer ends in `refused`: the screen asks whether to keep
  both (the file becomes a spare version). `import --album ADDRESS FILE --keep-both` does the same on the command line.
- `review --batch`: a **Page** column (the album's number, a link: ctrl/cmd-click in your terminal), the address as a link under the table
  with the album's year, credits and pages next to the file's format, pages and size; `p` over SSH copies the address instead of failing.
  The batch is shown folder by folder. Columns are now 1–8.
- `review --batch`: **click a column header (or press 1–8) to sort** the table; the same column again reverses it. Every column has a fixed
  width, so a rebuilt table never paints stale widths (Textualize/textual#6759).

### Fixed
- Albums of a series **without tome numbers** (`Les 40 commandements du jardinage`, `...du bricoleur`) were all stored under the series' title,
  `Les 40 commandements`: the album's own title is now read from the page's sub-title (and so is the tome, `16B6` or `INT`, instead of the
  address). `bdnex import --reparse` rebuilds the albums already stored, offline.
- An album whose page has **no `Tome` line** was stored as a one-shot with no series (`Rantanplan`, from `...-Tome-16B6-...`; 171 intégrales
  `...-INT-...`): the tome is now read from the album's address (`16B6`, `INT`, or a plain number), so the album keeps its series and
  shows it in `Album now`. `bdnex import --reparse` rebuilds the albums already stored, offline.
- `review --batch`: pressing `n` to refuse a question also loaded the next batch.
- `review --batch` shows a **loading panel** (a spinner, `weighing the files` and `loading the album list`, each with its own timer and a
  tick when done) where the table will be, instead of one grey line.
- **`review --batch` opens at once**: the files are weighed and the album list is loaded in background threads (the title says
  `weighed in N s`), instead of before the screen appeared.
- **`o` is a search box in the screen** (the album list filtered as you type: words in any order, accents ignored, a fuzzy match for a
  missing letter) instead of leaving the screen; `O` is the real `fzf` (the screen steps aside).
- **Linking is visible**: the row shows `linking...` and keys on it are refused until it is done, then the row shows `linked` with the new
  album, the new cover score and `you`; a refused link shows `refused`. The import's own messages no longer land on the screen.
- `bdnex candidates` without a query and without `--low` went through **every file of the library** (17,117 files with `--cover`: tens of
  thousands of requests). It now refuses and says so; `--all` says you mean it, and with `--cover` the first line says how many cover
  requests it could make.
- `candidates` says `[86/412]`: how many files it will look at is worked out first (and said: `412 file(s) to look at.`), so the number
  of a file always has a total.
- `candidates --cover`: a cover read from disk is announced like a download (no silent step), and the decision says whether
  the album whose cover is the file's is the one now linked, and, if not, the `bdnex import --album ADDRESS FILE` to type.
- **`bdnex candidates --cover` said nothing until the end**: it now prints each file as it starts (name, path, album now), each
  cover as it is downloaded (which album, which address) and compared (score, saved or downloaded, or why not), and what it
  decided, then moves on to the next file.
- **Colour everywhere, by meaning**: one set of roles (`ui/color.py`) is used by `candidates`, `review`, `ls`, `series`, `stats`,
  `trash`, `aliases`, `edit`, `mark`, `reset`, `relocate`, `init` and the log lines of `-v` / `-vv` (level and quiet rest of the
  line): names bold, good green, in doubt yellow, wrong red, the next command to type cyan, paths and explanations dim; scores
  in the colour of their value. Plain text in a pipe or with `NO_COLOR`.
- `review --auto` counted a switch to a better album as done when the import had refused it (the album already had another
  file): it now checks the library, says which files were not switched (they stay flagged, with the reason), and the summary
  says why the unconfirmed ones could not be judged (page not read, cover not compared).

### Changed
- **Every command's `--help` ends with examples** (what to type, and what it does), from one table (`bdnex/ui/examples.py`);
  a test checks that each example uses real options.
- **Default aliases**: bdnex ships eleven, with long names and an explanation, listed by `bdnex --help`
  (`settle-doubtful-matches`, `first-import`, `import-skipped-files-again`...). Your own `aliases:` are added to them (the same name replaces one; `NAME =` removes it).
- **`bdnex relink` is `bdnex relocate --find`** (find the files that were moved by hand, by their content; `relocate OLD NEW` is still for a moved folder). The old name still works, with a note.
- **`bdnex reparse` is `bdnex import --reparse`** (offline, from the stored pages; `--select TERM` limits it to some albums). The old name still works, with a note.
- **One command for the doubtful matches: `bdnex review`.** `bdnex recheck` is now `bdnex review --auto` (the old name still
  works, with a note). **Breaking:** the flags `--unlink`, `--unconfirmed`, `--rematch`, `--flag-unconfirmed` are gone: `--auto`
  switches a contradicted file to the better album found by the covers of its other candidates, else unlinks it; the
  ones nothing confirms are left alone. `--auto` now weighs the flagged files *and* the matches applied under the strong
  threshold, and prints, for each contradicted file, the covers it compared. `--json` prints the verdicts.
- **OpenCV is a normal dependency** (the cover check, `recheck`, the cover vote need it): `uv sync` and `pip install bdnex`
  install it. An optional extra was dropped by every plain `uv sync`, and the covers were then silently not compared.
  `bdnex[cover]` still installs (an empty extra). The Docker image grows by about 100 MB.

### Added
- **`bdnex review --batch N`**: a Textual table of the next N doubtful files (name, folder, album now, match score, cover
  score, state), with keys to keep, unlink, search another album in the album list, or enter an address. Every decision is
  saved at once (a `checked` mark), checked files are not shown again, so the review continues with another call. `o` uses
  `fzf` over the whole album list when it is installed (the screen steps aside for it), else a search box. Optional
  (Textual, a normal dependency); `--unmatched` adds the files without album, `--all` every file of the query, `--again` the ones already checked.
- **`bdnex aliases`** lists the aliases at work (bdnex's own and yours), and `--add "NAME = COMMAND  # WHY"` / `--rm NAME` edit the
  `aliases:` list of your configuration file for you (checked first, a `.bak` kept, `-n` previews).
- **Aliases**: `aliases: ["settle-doubtful-matches = review --auto  # what it does"]` in the configuration makes `bdnex settle-doubtful-matches` run `bdnex review --auto`, and `bdnex --help` lists it with its explanation.
- **`bdnex review --auto`** weighs every flagged file and every match applied under the strong threshold (the medium
  ones an older `--auto=all` applied unasked), with the album's page, the file's tags and the saved covers, and settles
  them by rule: confirmed, the flag goes; contradicted, switched to the album whose cover is the file's (the covers of its
  5 best other candidates are compared), else unlinked so that `import --skipped --auto=all` (strict, then the cover
  vote) decides it again; the others are left alone.
- **`import --auto=all` is strict by default** (**breaking**): it applies only strong matches without a doubt. A match in
  the middle used to be applied unasked (`match.auto_all: medium` brings that back); now the file stays skipped.
- **The cover vote** (`match.cover_vote`, `match.cover_vote_min`): at the end of the run, at low priority, the covers of
  the best candidates of the files left undecided are compared with the file's cover; one that looks like it (60%) and
  clearly more than the others decides the file (`cover vote 78%`). Needs OpenCV.
- **API:** `GET /api/candidates` (the albums each file could be, with the evidence and, with `cover=true`, the cover
  comparison), `GET /api/check?matches=true`, `POST /api/marks` and `marks` on an album and its files, `GET /api/fields`,
  `GET /api/history` (who changed what, from the page and the API), `GET /api/jobs/{id}/events` (a task's output as
  server-sent events), the review answers `rematch` and `album`, and the tasks `update`, `komga-show` and `komga-push`.
  `/api/albums` and `/api/files` are cut in the database and say how many match in all.
  **Breaking:** `GET /api/stats` is now what `bdnex stats --json` prints (it takes `q`).
- Match question: **I** enters an album Id (the number at the end of its page address) or an address, then shows the
  album for you to check (like beets' *enter Id*). It replaces the old **U** *Type the album's URL...* (**breaking** for
  that key only).
- `bdnex import --album ADDRESS|NUMBER FILE`: use that album for the file, without searching or asking.
- `bdnex stats [QUERY]` (albums read or only matched, files, formats, sizes, stored pages) and `bdnex fields` (the
  fields a query can use).
- Queries: a lone `,` is **or**, `field+` / `field-` sorts, and `bdnex ls --limit N`.
- `bdnex config` hides the secrets (`<hidden>`); `bdnex config --clear` shows them.
- `bdnex check --matches [QUERY]` weighs every match against its album page (the file's own tags, volume, year,
  authors, its folder) and lists the ones that look wrong; `--fix` flags them for `bdnex review`.
- `bdnex candidates [--low] [--cover] [QUERY]`: the other albums a file in doubt could be, with the evidence.
- Matching is **precision first**: a strong first choice is asked about when another album is within
  `match.min_margin` points, or when its page contradicts the file. On a 16,000-file library the strong matches that
  were wrong went from 0.4% to 0.08%. `tools/eval_matching.py` measures this on your own library.
- The parser reads the inking (`Encrage` is an Inker), the other credits Bédéthèque lists (preface, storyboard,
  background, design, adapted from), the icons of "Autres infos" and writes `Collection`/`Cycle` as ComicInfo
  `Imprint`/`StoryArc`. Run `bdnex reparse`, then `bdnex write`.
- `bdnex candidates` shows where each score comes from (the title score, then the volume, year and author), and the four
  adjustments are the options `match.volume_bonus`, `volume_penalty`, `year_bonus`, `author_bonus`.
- `bdnex mark` puts your own marks on files or albums (`--set checked=keep`, `--remove`), and any query finds them with
  `attr:checked` or `attr:checked=keep` (and `^attr:checked` for the others). **Library schema v10**: the first
  command after upgrading copies your library to `<library>.bak-v9` and then migrates it; an older bdnex can no longer
  open it.
- `bdnex update` reads the files again after another tool changed them (size, checksum, pages, damage, their own tags);
  the opposite of `write`. `bdnex check` also lists comic files of the collection folder that the library does not know.
- Path templates have functions: `%upper{}`, `%lower{}`, `%title{}`, `%capitalize{}`, `%left{}`, `%right{}`, `%asciify{}`,
  `%first{}`, `%if{}`, `%ifdef{}`; a typo in one is refused when the configuration is read.
- `hooks:` in the configuration (`after_import`, `after_write`, `after_move`): commands of your own that run afterwards.
- Docs: a *Command reference* generated from the commands' own `--help` (a test keeps it current), and *Checking the
  matches* (by hand or with an assistant).

### Fixed
- Albums stored with the old role `Encrage` (not yet re-read) lost their inkers in the Komga push and the written
  ComicInfo: the old name is read as `Inker`.
- CBR files that a `7z` without the RAR codec could not read (`Failed the read enough data` in the log, no cover, no
  write): `unrar`, `unar` and `bsdtar` are tried before `7z`.
- Ctrl-C during an import stops at once: the requests waiting for their polite delay give up (they kept the
  program alive for up to ten seconds each, so it took several Ctrl-C), and `import --database-only` no longer goes
  through the file-placement step, which looked up every imported file again for nothing.

### Changed
- `import -v`: the match score is a percentage in its own colour (red to green), and each cover comparison prints
  `cover: 87%  file name`.
- `bdnex komga push` leaves out the files whose match waits for a review (`--with-review` sends them): a doubtful
  match no longer puts another album's metadata, locked, in Komga.
- The web page's review answers and `bdnex review` share one function; a test checks every flag a web task builds
  against its command's `--help`.
- Covers are downloaded (and compared with the file's cover) in the background as each album completes, no longer
  all at the end of the run: an interrupted or still-running import already has the covers and scores of what it did.
  `bdnex candidates --cover` reads the saved covers instead of requesting them again.
- **Breaking:** `bdnex review --scan` is now `bdnex check --matches --fix` (the old spelling still works for a release);
  `bdnex review -l` is `--list`.
- **Breaking:** the import options `--no-fetch`, `--retry-partial` and `--recache` are gone (`--match`, `--fetch`,
  `--fetch --force`); the config option `match.use_facts` is gone (`bdnex config --update`).
- `bdnex --help` groups the commands by task.

## 0.10.0

- `bdnex komga show|push`: send the library's metadata (credits, summary, genres, series status and total...) to a Komga
  server through its API (Komga ignores the tags inside a PDF).
- The import fetches album pages while it matches, and builds each album as its pages arrive (`bdnex reparse` builds
  them offline from stored pages).
- A file's own `Web` address (CBZ, CBR or an attachment of a PDF) names its album when that album is in the album list.
- In-place repair keeps the damaged original in the trash; `bdnex completion` and the docs follow the commands.
