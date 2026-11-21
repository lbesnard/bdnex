# Changelog

bdnex is pre-1.0: commands, options and the library schema change, sometimes in ways that break. **Breaking** changes
are marked. Back up your library before upgrading (copy the library file while no bdnex
command runs, never while an import is fetching).

## Unreleased

### Added
- Review screen: `b` goes back to the previous batch (with its decisions), and `n` comes forward again.

### Changed
- Review screen: the next batch is weighed in the background while you work on the current one, so `n` (or the "next batch?" answer) shows it at once; the seconds counter of linked files no longer searches the list for every row.
- Faster on a big library (measured on a copy of a 16,000-album library): text searches (`bdnex ls WORDS`, the web page's search) take a third of the time (`fold` is cached and skips its accent loop for plain text); `bdnex series` and the next review batch are about a third faster; the web page's list of files only builds the credits of the rows it shows. The batch no longer asks the library for each file's `checked` mark and last import one by one.

### Fixed
- Import: a failure after a file was linked (an unexpected error, Ctrl-C) now takes the link and the album created for it back, as Back does; before the report said FAILED while the library kept the link. Going Back could also make the next file be taken for one already completed (its album page was then never filled); fixed.
- Review screen: a fetch no longer reconfigures the logging from its thread (log lines could appear twice in the panel, and later warnings went to a closed one), and the "Fetching ... (Ctrl-C leaves them)" line after quitting is written to the terminal, not to a redirected stream, so it is no longer lost.
- Review screen: a series with one album and no volume (a one-shot such as Scalp), given by its address or picked as a SERIES, links that album to the one file you chose when the file's name has no number; before it said "none with a volume number" and changed nothing.
- Review screen: in the `a` search each hit says in its own column whether it is a **SERIES** (each file gets its volume) or one **album**, with a header line above the list.
- Review screen: a link that fails (a file that moved, a locked library) is shown on its row and in the log; it no longer closes the screen. A series link no longer overwrites a file you decided while the question was open. The volumes box refuses digits like `²` instead of crashing.
- `bdnex review --batch`: the "about how many files" total no longer counts files already checked or passed over, so the "Check the next batch?" question is not asked when nothing is left.
- Review screen: `a` on one file whose album has no volume (a one-shot such as `OS`) links that file to the album you picked instead of refusing; a refusal is now in the log, and says to use `o` for one file.

### Added
- API: `GET /api/review/batch` returns the next files to check as `bdnex review --batch` shows them (doubtful, `unmatched`, `all`, `again`, a query, a size), and `POST /api/review/{file_id}/decide` keeps, unlinks or leaves any file of a batch, flagged or not, and marks it checked.

### Changed
- Review screen: the `e` form has **OK** and **Cancel** buttons (`Ctrl-S` is taken by tmux); `x` cancels when the cursor is not in a text field, `Esc` always does.
- Review screen: when the files you asked for all fit in one batch (a query that finds 3 files), deciding the last one no longer asks "Check the next batch?" (there is none): it says everything is checked and `q` quits.
- `bdnex review --batch`: closing the screen no longer drops the pages and covers still queued for the files you linked: they are fetched before the command returns (Ctrl-C leaves the rest to `bdnex import --fetch`), and a file whose import fails (a corrupt archive) is reported on its row without stopping the files queued after it. Before, a failed import said "page and cover fetched".
- Review screen: a state that waits for the site turns a wheel (`⠹ linked · fetching 12s`), and giving the address of a bdtheque.com page shows `⠹ fetching the page 3s` in the state column while the page is read (it said `no album` until the link was made).
- File names with a scan's quality tag glued to the number (`Death_Note_-_003lq.cbz`) now have their volume (`lq`, `hq`, `hd`, `sd` are dropped); before, the review screen's `a` said "the files ask for no volume".
- Review screen, `a` with a series page: files numbered mostly past the series' last volume (chapters, not volumes: files 3 to 42 for a 12-volume series) are refused with that explanation instead of linking the few numbers that happen to match.
- Review screen: it could freeze (until the database's 30 s timeout) after linking files while a page was being fetched in the background: the background import's log line waited for the screen while holding the library's write lock, and the screen waited for that lock. Log lines no longer make a thread wait.
- Review screen: mark all / none moved from `ctrl+a` (taken by tmux and some terminals) to `m`.
- Review screen: closing the album search before its answer arrived could crash (`NoMatches: OptionList`).
- Review screen: `y` copies the whole log to the clipboard, and the log panel follows new lines only while it is scrolled to its end (scroll up to read or select).
- Review screen: the state of a file whose page and cover are being fetched counts the seconds (`linked · fetching 42s`, or `queued` while the files before it are fetched) and the log panel says which file is being worked on and how long it took, so a long wait is visible.
- Review screen: picking an album for a file the import skipped no longer reads (hashes) the file before the link is written, which took seconds on a network disk: it is registered unread and the background import fills the rest in. The log panel says how long the write took.
- Review screen: linking a file the import skipped (no record in the library yet, the `--unmatched` rows) crashed; it is now registered with its link (also for `e`).
- Review screen: the title says which batch of about how many (`batch 3/53`). The `o` search is quicker: it waits for a short pause in the typing, skips searches already typed over, and looks for the words of a longer text among the albums of the shorter one (and remembers the last answers).
- Review screen: **the match is written at once.** Picking an album links the file in the library right away (a spare version when the album has another file, no question); the page and the cover are fetched afterwards, in the background, one file at a time, and the screen stays free: go on to the next file or batch, or press `u` to unlink (which cancels what is queued). Quitting with fetches left says so (`bdnex import --fetch`).
- Review screen: a link now runs the import with `-v`, so the log panel says each step (album page from the library or fetched and how long, cover saved or downloaded); the level was reset to warnings after the first link.
- Review screen: a link to an album that already has another file is made at once, as a spare version (no refusal, no question): which file stays is the job of `bdnex duplicates`. A file already linked to that album is reported as linked, not refused.
- A series page whose albums are all collected editions or one-shots (no `Tome` in their addresses, e.g. Fell) listed no album at all: the series' own name, from its address, now tells which links are its albums.
- Review screen, attaching files to a series by its page address: when no file finds its volume, the message says what the page lists and what the files ask for (a series of collected editions has no tome 2), instead of the generic "not in a series".
- `ls --sitemap --for FILE`: the file's words count as whole words of an album's name (`fell` is not in `Rocketfellers`), and albums that tie at 100 are still told apart by them.
- Review screen: a series address that is not stored is fetched with your fetcher (once, then stored), instead of failing with "the network is not used".
- Review screen: the next batch (`n`) never shows again a file of the batches before in the same run (a file passed over with `s`, or without album, used to come back).
- The review screen no longer crashes when a link ends after the next batch was loaded.
- `import --album` (and so a link in the review screen) read the album and series pages from the site again every time, through the fetcher: minutes per file. It now uses the stored pages; only `--force` asks the site again. And `import --force` no longer downloads an album page twice (the page fetched ahead was fetched again when its turn came).
- `bdnex import FILE` finds a file by the path `bdnex ls` prints (relative to `collection_path`) when it is not found from where you are.
- A link made in the review screen no longer asks questions (a cover that looked different asked one the screen could not show, so the row said `linking...` for ever).
- The review screen no longer waits for ever on a link: after 120 seconds without an answer (a slow site or fetcher) the file can be decided again, and a late answer is ignored.

### Added
- **bdtheque.com as a second place to take an album from** (schema v13, **back up the database first**): give the address of a series page (`https://www.bdtheque.com/series/14922/...`) in the review screen (`i`) or with `bdnex import --album ADDRESS FILE`; the page is fetched with the same fetcher as Bédéthèque's (all its endpoints), kept compressed in its own `bdtheque_pages` table and read into an album with `source: bdtheque`. Only for addresses you give: nothing searches the site.
- Review screen: `e` writes the metadata of a file by hand, as an album of your own (`source: manual`) linked to it, for comics the site does not know: a form for one file, or one form for the series of several marked files (a volume per file, read from its name and editable).
- Review screen: a log at the bottom (`v` hides it) shows what bdnex is doing while it looks idle: pages read or fetched, links, and the output of the import; `-vv` adds the debug lines. `x` unmarks everything.
- A volume glued to the title (`SOA3.pdf`, `SonOfAnarchy6.pdf`) is read as the volume when the name has no other number, for the matcher and for attaching files to a series.
- Review screen: `ctrl+a` marks every file not decided yet, and again marks none; files being linked drop their mark, so the next series can be marked while they link.
- Review screen: `a` (attach files to a series) and `i` accept the address of a series page (`bedetheque.com/serie-N-BD-Name.html`); the files get the album of that series with their volume number.
- **`bdnex ls --sitemap WORDS`** searches the whole album list (every album of the site, not only yours) the way `fzf` does in the review screen: all the words in the name, a typo forgiven, 20 results, nothing requested. **`--for PATH`** gives the albums a file could be, by its name, its two last folder names and its volume (the right volume and the file's words first, with the reason). Both take `--json` and `-c`; the review screen and the command share one search (`importer/album_search.py`).
- `tools/eval_covers.py` no longer judges a cover comparison by a guess: besides the matches that something confirms, it compares each file with a cover of ANOTHER SERIES and of the nearest OTHER VOLUME of its own series (wrong for sure), and reports the AUC and the catch rate against each.
- `tools/eval_covers.py`: measures how well a cover comparison tells a right match from a wrong one on a real library (the answer key of `eval_matching.py`): the current score and
  five others (looser ratio, RANSAC geometry, edges trimmed, dHash, colour), with their AUC and the share of wrong matches caught when only 1, 2 or 5 % of the right ones are flagged.
- **`bdnex mirror`** keeps the whole catalogue in the library: it compares the album list with the library and fetches every album that is
  missing (or only matched) with its series page and its cover, through the same endpoints and polite delay as an import, several at once.
  `-n` counts what is missing and the room it takes; `--limit N`, `--covers-only`, `--no-covers`, `--workers`. Stop and run again at any time.
- Stored album and series pages are **compressed** (about a tenth of the size); pages stored before stay readable. *Back up the database
  first*: an older bdnex can not read the compressed pages. The first bdnex command after the upgrade compresses the pages stored before,
  once, as a library upgrade (schema v11): the old database is kept as `<library>.bak-v10` and the older bdnex refuses the new one.
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
- The question "the cover looks little like the album's" (asked during `import`) was one plain line that was hard to read. The file, the album and the score are now on their own lines, in colour (the file and album in bold, the score in red), and the question is just "Keep this match?".
- **`cover.match_percentage` is 3, not 40.** A right match scores about 50 on the cover comparison (the share of distinctive features found in both covers), so 40 flagged
  one right match in five. Measured with `tools/eval_covers.py` on a real library (each file against its own album's cover, another series' and the next volume's): AUC 0.98 and 0.95;
  at 3 the cover flags 5 % of the right matches, catches every wrong-series match and about 43 % of the wrong-volume ones. If you set the option yourself, your value stays.
- **Matching: which collected edition.** `Intégrale 2` / `Intégrale T02` now goes to `...-INT2-...` (+5, like a volume), a plain `(Intégrale)` prefers the one collected
  edition of the series (`INT`, +8) to one of several (`INT1`, `INT2`: +3). On a real library, of the 309 files that say intégrale 20 change their first choice
  (`Face de lune - Intégrale T01` -> `INT1`, `Rork - intégrale 02` -> `INT2`...), 15 of them are asked about rather than applied. A file that says only
  `Intégrale` while the series has several (`Double masque INT1/INT2/INT3`) is a coin toss between them: it is asked about.
- **The cover phase of an import says what it is doing.** `Getting 5965 cover(s)...` was followed by an hour of silence: the covers were requested one after the
  other, with no output. They now come 2 to 4 at a time (one per endpoint, each request still politely spaced), with a progress bar with the time left
  (`Covers [██░░] 34%  2,031/5,965  about 41 min left`), the file being weighed with `-v`, the covers that did not come named with `-v`, and a line at the end
  (`Covers: 5,830 done, 135 not downloaded`). Ctrl-C stops; `bdnex import --fetch` goes on.
- `Corrupt JPEG data: 1 extraneous bytes before marker 0xdb` no longer lands on the screen once per picture: it comes from the JPEG library inside OpenCV, writing to the
  process's stderr; it is now caught while a picture is decoded and kept for `-vv`.
- **Matching: a file that says "intégrale" is matched to the collected edition**, not to a single tome that has the same words (`Universal War One (Intégrale)` was linked to tome 5).
  An album whose address says `INT` gets +8 and, when one is among the candidates, a single tome gets -8 (only when the file gives no volume). On a real
  library, of the 309 files that say intégrale 4 change their first choice, all to the collected edition (`Double masque INT3`, `Madila INT`,
  `Un petit livre oublié sur un banc INT`, `Universal War One INT`); nothing else moves. Run `bdnex review` / `import --match --force` on the files you suspect.
- **Parser, found by auditing the 16,449 stored pages of a real library**:
  - a tome the site writes with a minus (`Donjon Potron-Minet` -82, `Donjon Antipodes` -10000) was read as a special (`-82`) and lost its volume: it is the number again;
  - the address no longer numbers the albums of the `(AUT)`/`(DOC)` miscellanies (`Manara` #1992 is a year) and no longer gives the site's filler as a title (`Integrale 1`: the series name stays the title).
  - `import --reparse` now takes back what an older parser stored for those: the series and the number of a `(AUT)`/`(DOC)` album, and a special stored next to a number
    (the library otherwise keeps what the parse lacks). On a copy of a real library: 137 albums updated, and the audit then finds no album that differs from its page.
- `import --reparse` shows **a progress bar with the time left** (`Pages [████░░░░]  17%  101/584  about 18 s left`) for the reading, then a second step for the
  update, instead of a line every few hundred pages.
- **`import --reparse` is about 15 times faster** (a real 16,400-album library: 4 minutes instead of about an hour). A series page was parsed again for each of its
  albums (60% of the time), and the album pages were read one at a time: now a series page is parsed once, the albums are read in several processes (`--workers N`, default one
  process fewer than the cores), one series to a task, and the library is written afterwards, in one process. Changing only the order of the names of a credit
  is no longer listed as an update.
- `import --reparse`: the numbers of the `(AUT)` (author) and `(DOC)` pseudo-series are no tome (a year, a position: `Mœbius` became volume 1984), so those
  albums keep their title from the page but no series and no volume, as before. An album with no title of its own (the page says `Tome 9`,
  `Volume 2`, `Intégrale`) keeps the series name as its title, not the site's filler. And a series page that was never fetched, missing while working offline, is
  no longer a warning for each album (`bdnex mirror` or `import --fetch` fetches those pages).
- **Candidates' covers that gave a 404** (`Nikopol INT La trilogie Nikopol`): for an album whose page was never read, bdnex guessed the cover
  address (`Couv_<id>.jpg`), which is wrong for about one album in five (3,197 of the 16,402 albums read so far have another name). When the guess
  is not there, the album's page now says where the cover is (one request, the page is kept) and that address is tried. Same for the cover vote of an import.
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

### Added
- **Series index** (schema v12, **back up the database first**: an older bdnex refuses it): `bdnex init` also reads the 27 letter pages of the site (`bandes_dessinees_0.html` ... `_z`), every series by name, into a new `series_index` table; `--skip-series` / `--series-only`. The review screen's search for a series (`a`) and `bdnex ls --sitemap WORDS` search it besides the albums. Series pages are still read on demand only.

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
