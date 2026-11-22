# Changelog

bdnex is pre-1.0: commands, options and the library schema change, sometimes in ways that break. **Breaking** changes
are marked. Back up your library before upgrading (copy the library file while no bdnex
command runs, never while an import is fetching).

## Unreleased

### Changed
- **License: GNU Affero General Public License v3 or later** (it was MIT in `pyproject.toml` and CC0 in `LICENSE`, which disagreed). You may use, change, share and sell bdnex, but whoever distributes it, or lets others use it over a network, must give the full source, changes included, under the same license. It is also the license of PyMuPDF, which bdnex builds on and the Docker image bundles. **Versions up to 0.13.0 stay under the terms they were released with.** The web page shows a "Source code · AGPL-3.0" link, and the image carries the license label.
- **The page fetcher is not Bédéthèque's.** Its options moved to a new `fetch:` section: `fetch.endpoint` (was `bedetheque.fetcher_endpoint`), `fetch.parallel`, `fetch.delay` and `fetch.fetcher` (was `bedetheque.fetcher`, only for a function of your own). **Setting `fetch.endpoint` is now enough to use FlareSolverr**; without it (and without your own fetcher) the saved pages are read, as before. The old `bedetheque.*` keys still work, with a warning, for now: **`bdnex config --update` moves them in your file** (a list over several lines, trailing comments and all; the default `fetcher` line is dropped). The code is `bdnex/fetchers.py`; `bdnex.importer.bedetheque:flaresolverr_fetch` and `bdnex.importer.bedetheque.fetchers` keep working as the same module. `bdnex mirror`'s `--workers` follows `fetch.parallel`. Back up your configuration before `config --update` (it keeps a `.bak` too).
- Review screen: when you give `a` a series page and none of the marked files finds its volume on it (a page that lists volumes 1 to 4 for files numbered 5 to 25), the message now says in words what each side has ("the page lists volume(s) 1 to 4; these 21 file(s) ask for 5 to 25. They look like another series than this page's, or one the site does not have"), in the popup and not only in the log, and **offers to write those files by hand as that series**: yes opens the `e` form with the series name and each volume read from its file name. Partial matches are unchanged.
- Review screen: with several fetcher endpoints (`bedetheque.fetcher_endpoint`, or `bedetheque.parallel`) the page, series page and cover of **several linked files are downloaded at the same time**, as many as `bdnex import` fetches (each request still waits its polite delay), and every file being fetched shows the turning wheel. The library is still written by one thread, one file at a time, so two files of one series never write the same rows at once. With one endpoint nothing changes (one file at a time).
- Import is a little cheaper per album: the series an album left (the placeholder made from its address before its page was read) is deleted by an indexed lookup instead of a scan of every album (2.6 ms to 0.013 ms on a 16,000-album library, per album fetched), and a page already stored is found without decompressing it.
- `bdnex import` opens the library once for the whole result list instead of once per line (thousands of opens on a big run), and the review screen no longer keeps everything its background imports print in memory for the whole session.
- Starting a command that needs the album list (`import`, `candidates`, the review screen's album search, `ls --sitemap`) is about 3.4 s faster on a list of 372,000 albums (4.1 s to 0.65 s): the parsed list is kept in the library (a `source_cache` row of kind `index`, about 14 MB, no schema change) and read back while the stored sitemaps are the same ones; `bdnex init` rebuilds it when they change. A read-only library just parses as before.
- Code: what `bdnex candidates` finds (`iter_report`, `build_report`, `compare_covers`, ...) moved from the command's module to `importer/candidates.py`; `review --auto`, the album search and the web page's candidates view no longer import from a command. The command and its output are the same.
- `bdnex mirror` is now `bdnex init --mirror`: the same job, after the album list it needs. It asks before it fetches (the preview says how much: `-n`), `--yes` answers for a script, and without a terminal it refuses to start without `--yes`. `--skip-list` uses the list already here; `--limit`, `--no-covers`, `--covers-only`, `--workers` and `-n` moved over. `bdnex mirror` still works (it is `init --mirror --skip-list --skip-series --yes`) and says what it became.
- `bdnex fields` is now `bdnex ls --fields`, next to the query syntax it explains; `bdnex fields` still works and says so.
- Album search (the review screen's `o` and `a`, `bdnex ls --sitemap`) is faster on a big album list: on a 372,000-album list a word search takes about a third of the time (the rarest word is found by `str.find` over the whole list), and typing letters of an `fzf`-style search narrows what the shorter text found instead of scanning the list again. The answers are the same.
- Faster on a big library (measured on a copy of a 16,000-album library): text searches (`bdnex ls WORDS`, the web page's search) take a third of the time (`fold` is cached and skips its accent loop for plain text); `bdnex series` and the next review batch are about a third faster; the web page's list of files only builds the credits of the rows it shows. The batch no longer asks the library for each file's `checked` mark and last import one by one.
- `bdnex duplicates --covers` is faster: the cover hashes are stored a few hundred per commit (not one disk sync per file), only the columns it needs are read, and similar covers are looked up by blocks of the hash instead of comparing every pair (about 3x on 16,000 files, the same groups).
- Review screen: the next batch is weighed in the background while you work on the current one, so `n` (or the "next batch?" answer) shows it at once; the seconds counter of linked files no longer searches the list for every row.
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

### Added
- Review screen: after `a` (a series page for the marked files) the files say `⠹ reading the series 3s` in their state column, with the turning dots and the seconds, until the series page is read (it is fetched once: a few seconds), instead of nothing happening; they can not be decided again meanwhile, and the state is cleared when the answer is here or the read failed.
- **Docker image on the GitHub Container Registry**: each release (the Release workflow) builds and pushes `ghcr.io/lbesnard/bdnex` for amd64 and arm64, tagged with the version and `latest`. `compose.yaml` now pulls it (`BDNEX_VERSION` pins a release; `docker compose pull` updates); `compose.build.yaml` builds from the checkout instead. CI builds the image on every change and starts it for real (healthz, the login, the config and library on the volumes).

### Fixed
- Bédéthèque pages: a credit label written a little differently (`scenario`, `SCÉNARIO :`, `(Scénario)`, `traduction`) is the role it stands for instead of an unknown one; a block of the former page layout that has nothing in it, or an image with no address, no longer makes the whole album unreadable (it raised and the album was lost); a letter page cut short on the way (it stops before its closing `</html>`) is no longer stored: it replaced the letter's series with the short list, and was read again as it was. The error says to run it again (a page that announces more series than it lists is still only a warning).
- Komga: `komga push` leaves alone a field that is **locked in Komga** and differs (someone locked it there on purpose, or an earlier push did and it was changed since) and says how many (`--overwrite-locked` changes them too); a push stops after five failures in a row (a server that is down) instead of asking for every book, and says that running it again finishes the rest (it only sends what still differs).
- Library: two processes opening an old library at once (the web page and a command, two commands) both ran the schema upgrade, and the second failed on a column that was already there: the upgrade now takes a lock on the library file, so the second waits and finds nothing left to do.
- `::regex` queries refuse a pattern that can run for ever (a quantifier inside a group that is itself repeated, such as `(a+)+`; or more than 300 characters), and only try the first 2000 characters of a value: a pattern like that on a long summary could hang the screen or the web page.
- Review screen: a last column, **Would go to**, shows where `bdnex move` would put each file (relative to the collection, from the `paths` templates and the album the file has now), so a match can be judged by the place it would send the file to; it is also in the detail panel, sorts with `9`, and is cleared when the row is decided again.
- Review screen: **a question that comes by itself waits in a queue** (`w` opens it) instead of opening over what you are doing: the answer to `a` (link the files to the series, or write them by hand) and "check the next batch?" no longer cover the hand-written form you are filling (two windows over each other, and a key meant for one answering the other). A red mark at the top right says how many wait (`● 2 questions waiting · w`); `w` opens the oldest, and says to finish the open window first when there is one.
- `bdnex ls --unmatched --json` (and `--missing --json`) print the rows as they are: `best`, a padded screen column (`'    -'`, `' 83.5'`), was added to them next to the real `score`; `--limit` applies to these two listings too.
- `ls`, `ls --unmatched/--missing`, `series` and the trash listing open the library read-only (they ran a migration, rewrote stored paths and made a `.bak` copy just to list), and say so when there is no library instead of creating an empty one. `bdnex stats` no longer loads the authors and genres of every file to count them.
- Shell completion: the flags of a command are read from its options list only (`trash` was offered `--match` and `--force` from its examples); it no longer goes through the configuration check (an invalid configuration gave every command no flags and still printed a script, a first run created the config file); file names with spaces complete as one name in bash and zsh; zsh loads `compinit` before `bashcompinit`.
- Pictures: a cover (or the first page of a file) whose header says it has more than 100 megapixels is refused before it is opened (a 30000 x 30000 PNG of a few KB took gigabytes to decode and ended the import or the review screen); tmux's `allow-passthrough` is asked once per session, not before every match question.
- **Docker image on the GitHub Container Registry**: each release (the Release workflow) builds and pushes `ghcr.io/lbesnard/bdnex` for amd64 and arm64, tagged with the version and `latest`. `compose.yaml` now pulls it (`BDNEX_VERSION` pins a release; `docker compose pull` updates); `compose.build.yaml` builds from the checkout instead. CI builds the image on every change and starts it for real (healthz, the login, the config and library on the volumes).
- Web page: following a job no longer ends at the first failed request (a proxy hiccup, a server restart): it says so once and asks again, with a growing pause; leaving the job's page ends its polling and takes its keyboard listener off the page (pressing `s` or `a` elsewhere used to answer the job's question), and two followers of one job can no longer run at once. Every view gives up when a newer draw started while it waited for the server (a slow answer for what was typed before, a click on another view or twice on "Show more" rendered over the newer one). Covers fetched with a token send the page's own header (no browser login box on a 401) and release their picture's memory.
- Release workflow: it runs on `main` only, tests the exact commit it releases (Python 3.10 and 3.13, with a read-only token), builds the wheel and the Docker image first and pushes the version bump with its tag together (`--atomic`) only afterwards, so a failed build leaves no tag and no bumped `main`; the bump type goes through the environment (never pasted into a script) and only the project's own version line is rewritten; two releases can not run at once. Every action is pinned to a commit, and CI's token is read-only.
- `tools/demo_library.py` gives the same album ids on every run (a second run added every album again).
- **Docker image on the GitHub Container Registry**: each release (the Release workflow) builds and pushes `ghcr.io/lbesnard/bdnex` for amd64 and arm64, tagged with the version and `latest`. `compose.yaml` now pulls it (`BDNEX_VERSION` pins a release; `docker compose pull` updates); `compose.build.yaml` builds from the checkout instead. CI builds the image on every change and starts it for real (healthz, the login, the config and library on the volumes).
- Review screen: a decision (`k`, `u`, `l`, a link) that cannot be saved (a locked library) is said and the row stays as it was, instead of crashing the screen or showing a decision that was never saved; `s` on a row already decided no longer rewrites what the table says; `l` on a file the library has no record of is remembered (it was shown again in the next batch).
- Review screen: a newer decision (a page address of another site, a hand-written album, a series of marked files) supersedes the page and cover still queued for the older link, and the row stops waiting for them (it stayed "fetching" for ever); the fetch queue tells a job whose start fails and goes on with the others (a failure inside it used to end its thread silently), and after `q` / Ctrl-C a one-by-one queue starts no further import.
- Review screen: Enter in the album search no longer picks a hit of what was typed *before* (it linked the file at once); the hand-written album form says "year ..." or "a series needs its name" before it closes, so nothing typed is lost, and a trailing newline in the volumes is not a file; a worker that fails (an address lookup, the series page, the cover screen closed while it reads) is said instead of ending the whole program; `b` after a batch that could not be weighed no longer replaces that batch with an empty one; Ctrl-O (like `p`) starts no browser over SSH or where there is no display, and copies the address instead.
- `bdnex review --batch 0` is refused instead of being read as no batch.
- **Docker image on the GitHub Container Registry**: each release (the Release workflow) builds and pushes `ghcr.io/lbesnard/bdnex` for amd64 and arm64, tagged with the version and `latest`. `compose.yaml` now pulls it (`BDNEX_VERSION` pins a release; `docker compose pull` updates); `compose.build.yaml` builds from the checkout instead. CI builds the image on every change and starts it for real (healthz, the login, the config and library on the volumes).
- Trash: `bdnex trash --empty` and `--restore` (and the page) trust a record only as far as it points inside the trash folder (a record that leads to a comic elsewhere is dropped, never deleted or moved); a trash folder that is not there (a disk not mounted) keeps its records instead of forgetting the files; a file that can not be deleted keeps its record and the others go on (exit code 1, with the reason); a restore never writes into a read-only (`copy`) source and puts the file back in the trash if the library can not register it; a move to the trash is written down first (a locked database left an unknown file in the trash); `--older-than` without `--empty` is an error.
- Folder watch: a look that raises no longer ends the watch thread silently; a run that fails doubles the wait before the next look (up to 64 times the interval) and a success resets it; no run is started while none of the import sources is there (an unmounted disk).
- Archives: an archive is refused when a member is over 512 MB, packs like a zip bomb (more than 100 MB at over 200 to 1), holds more than 16 GB or 50,000 members (also when its header lies: reads are cut at the limit), and names with `..`, a leading slash, a drive or backslashes are cleaned before they are written to the new CBZ (a Zip Slip entry for whatever unpacks it later).
- Web tasks: a symbolic link inside the collection that leads outside is no longer a path inside it; `apply-album` takes only an album number or an address on bedetheque.com / bdtheque.com (any other address would have made the server fetch it for whoever is logged in). Hooks are no longer handed `BDNEX_PASSWORD` and `BDNEX_TOKEN`.
- **Docker image on the GitHub Container Registry**: each release (the Release workflow) builds and pushes `ghcr.io/lbesnard/bdnex` for amd64 and arm64, tagged with the version and `latest`. `compose.yaml` now pulls it (`BDNEX_VERSION` pins a release; `docker compose pull` updates); `compose.build.yaml` builds from the checkout instead. CI builds the image on every change and starts it for real (healthz, the login, the config and library on the volumes).
- Import: a file in a read-only (`copy`) source is never moved to the trash as a "replaced" duplicate; Ctrl-C while the cover vote applies a choice takes the half-done link back (as for any file); Ctrl-C during the covers stops the run (no cover vote, no traceback) and, like Ctrl-C during the fetch, nothing is moved or written afterwards (`import --move` used to go on and place the files).
- Import: going Back no longer leaves the cover check of the old album to run against the album you chose next (it could flag or unlink the file); a complete album that was already in the library gets the same offline second look as a new one; a spare file linked again where it is alone stops being a spare (and an undo gives it back); a file moved or deleted outside bdnex is no longer a duplicate of the same bytes elsewhere (the import skipped it for good).
- Import: `import -n` opens the library read-only (no creation, migration or backup, as documented), and says so when there is no library or no album list; a matching worker that never answers (killed out of memory) fails its file after 15 minutes instead of hanging the import, and the workers are forked when matching starts, before any thread of the run exists.
- **Docker image on the GitHub Container Registry**: each release (the Release workflow) builds and pushes `ghcr.io/lbesnard/bdnex` for amd64 and arm64, tagged with the version and `latest`. `compose.yaml` now pulls it (`BDNEX_VERSION` pins a release; `docker compose pull` updates); `compose.build.yaml` builds from the checkout instead. CI builds the image on every change and starts it for real (healthz, the login, the config and library on the volumes).
- Two tests that failed now and then on CI (not on a developer's machine) wait for what they depend on: the review screen's "unlink wins over the late import" test now waits for the download queue (its own thread, which Textual's workers do not cover), and the Komga task test waits for its first job to end before starting the second (one job runs at a time, so the second was refused on a slow machine).
- **Docker image on the GitHub Container Registry**: each release (the Release workflow) builds and pushes `ghcr.io/lbesnard/bdnex` for amd64 and arm64, tagged with the version and `latest`. `compose.yaml` now pulls it (`BDNEX_VERSION` pins a release; `docker compose pull` updates); `compose.build.yaml` builds from the checkout instead. CI builds the image on every change and starts it for real (healthz, the login, the config and library on the volumes).
- Docker: the saved pages (`bedetheque.pages_dir`) were under the container's temporary home and lost at each restart: they are in `/data/pages`. A missing or not-writable `/config`, `/data` or `/covers` (Docker creates missing ones as root) is named at start, with the way out, instead of a traceback.
- Matching: a year or an author found on an album's page no longer lifts a match above the cap of a wrong or missing volume (a wrong-volume album could reach 90, "strong"); the series-only cap holds too. Measured with `tools/eval_matching.py` on 1500 linked files: wrong first choices among those scoring 85 or more went from 6 to 4, none among the strong ones before or after, and 2 files fewer are applied unasked (266 to 264).
- Matching: `Intégrale 2` (no `T`/`Tome`) asks for the second collected edition, so tome 2 no longer gets the volume bonus; `T`/`Vol` followed by a three-digit number only count as a volume when glued (`T105`: `Le Vol 714 pour Sydney` is a title); `Cœur` and `Œil` are searched as `Coeur` and `Oeil` (the ligature was dropped, leaving `c ur`).
- Covers: a cover with too little detail to describe (a flat or dark one, a tiny placeholder) is "not compared" instead of scoring 0 (which flagged right matches as "the cover differs") or a lucky high score. The cover vote decides nothing when a competitor's cover did not come, or when the best cover is of a volume the file does not say. Duplicates: two first pages that are blank or uniform are no longer "the same book" by their hash.
- Checking matches: a year one off the album's confirms nothing by itself (a reissue, another album of the series). Folders: a candidate scoring under 50 is no vote for its series, and `les`/`des`/`the`/`tome` no longer make two series look alike.
- Configuration: `match.strong_threshold` below `match.medium_threshold` is refused. `tools/eval_matching.py` works again after the `fetch` move.
- Questions: a `[2]` or `[x]` inside an album's title or a file name is text, never a key (it could replace the real key of an answer, or stop a write run with "the key is used twice").
- No question is asked without a terminal any more without saying so (`NeedsTerminal`, as for the match question), for every kind of question, not only some.
- Ticking the files of a series and pressing Ctrl-C skips the folder instead of meaning "none of them belong"; an album Id the source can not look up is said and asked again (and a number with spaces never reaches the source); `fzf` vanishing after it was found no longer crashes the search.
- The page's search of the album list never starts `fzf` on the server.
- Where to put imported files from outside the collection offers Copy first (Enter no longer moves files out of your sources).
- Progress bar: honours `NO_COLOR` and `TERM=dumb`, cuts the line by terminal columns (wide characters, accents), flushes when it clears. `FORCE_COLOR=0` means no colour. A log line has the same text with and without colour (the level was padded only with colour). Setting up the logging again (each job of `serve`) replaces only bdnex's own handlers instead of every handler of the process.
- A series you renamed keeps its name when another album of it is imported later (the site's name used to come back, and the rename flipped back and forth).
- `bdnex edit`, the editor and the web page agree on a value: a string for `genres` or a credit role is a list split on `;` (as with `--set`); a credit role must be a known one (`writer` becomes `Writer`, `Auteur` is refused); text is one line without control characters and not longer than 1000 characters (20000 for the summary); `isbn` and `alternate_number` written as a bare number that YAML would change (`0123456`, `1.10`) must be quoted.
- `bdnex edit` refuses a query that would change more than 2000 albums (as the web page does) and, when one album fails, says which and finishes the others.
- An album with a hand edit or a mark is no longer deleted when its last file is unlinked (the edits and marks went with it).
- Series written by hand: two names that fold alike (`C++`, `C#`) are two series, one name is always one series, and a name with a comma (`Dupuy, Philippe`) is not split when the form is saved again.
- Mark names reject a trailing newline, and values with control characters.
- Config: a typo inside a list entry (`import.sources[].mod`, a `paths.rules` entry) is named in the error instead of crashing every command with a traceback.
- Config: `config --update`, `aliases --add/--rm`, the first-run file and the settings page write the file the same safe way: beside it, flushed, then moved over it (a crash never leaves a half file), the `.bak` copy keeps the file's 0600 mode, and a config that is a symbolic link (dotfiles) stays a link.
- Config: a comment of your own like `# Note: ...` no longer ends a section for `set_value` / `config --update` (which wrote an option twice), an active section wins over a commented one of the same name, a `#` inside a quoted value is not taken for a comment, `config --update` does not rename `directory` when `collection_path` is there, and Unicode line separators inside a value are kept.
- Config: an alias named like a command a newer bdnex added is ignored with a warning (the command wins) instead of making the whole config invalid; `aliases` lists a broken entry instead of crashing, and `aliases --rm NAME` removes a switched-off or broken one.
- Config: a source that is a symbolic link to the collection is refused; a relative `collection_path` is refused where it is used; a password in `komga.url` and secret-looking keys of `plugins` are hidden by `bdnex config` and the settings page.
- `bdnex undo` checks the library too before it moves a file back (a file the library forgot, or a stale row at the old place, is refused), and when the library cannot follow the move it puts the file back, so disk and library never disagree.
- `bdnex relocate OLD NEW` also rewrites the paths kept in the trash, the `undo` list, the files waiting to be moved in and the copies' origins (`trash --restore` and `undo` no longer recreate the old folder).
- Rewriting a file keeps the `<Pages>` list (page types) an existing ComicInfo.xml had, and the permissions of the file it replaces.
- Ctrl-C while a file is being written no longer leaves a `.bdnex-tmp` beside the album.
- A worker process killed (out of memory) fails only the file it was working on; the others go on in order. Files whose destination differs only by case are never worked on at the same time.
- FlareSolverr fetcher (`flaresolverr_fetch`), from the review of the sources: it now reads the **HTTP status the site answered** (`solution.status`). A 404 or 410 is reported at once as "the page is not there" (no other endpoint is asked, and none is marked down); a 429, 5xx or other error status is no longer returned as a page (the body used to be cached as the album: an empty title, no credits) and the next endpoint is tried; a **malformed answer** (no page in it) is treated like any failed endpoint, so the next one is still tried instead of the fetch dying on an `AttributeError`. Answers without a status are read as before.
- Komga, from a security and correctness review: **`komga.url` can no longer be changed from the web settings page** (a new address received the stored API key or password on the next push); the address must start with `http://` or `https://`; a **redirect from Komga is not followed** (the `X-API-Key` header would go to the other host, and a PATCH answered by a 301 could be replayed as a GET): it is reported with the address to use.
- Komga pairing: a file whose size differs is no longer paired **by name alone** unless the folder it is in has the same name too (a generic `01.cbz` of one series took another series' metadata, locked); names are compared composed and without case.
- Komga: a Komga folder that holds several series (or several one-shots) gets no series data any more (one title, summary and status would be wrong for some of its books); the report says so. The authors a book has in Komga for roles the library has no names for are kept, and series genres are added to Komga's (as tags already were), not replaced. A status such as `Non terminée` is no longer read as ended, nor `Discontinuée` as ongoing; a volume stored as a float is sent as `1`, not `1.0` (it was pushed and re-locked on every run). Books are listed to the last page even when Komga omits the `last` flag, a book shifted by a change during the listing is not listed twice, and the deprecated list call is used only when the server does not take the new one (not after a passing 500).
- `bdnex init`: a sitemap fetch that comes back as an error page (a 502, a rate limit, a challenge) is no longer taken for the end of the album list. It was stored as a good sitemap and everything after it was silently missing, for every later run and in the stored index; now only a real, empty `<urlset>` ends the list, anything else is refused (nothing stored, with a message), and a bad file stored by an earlier version is fetched again by `bdnex init` (and the list says where it stops until then).
- A page that Cloudflare served normally, and that carries the `/cdn-cgi/challenge-platform/scripts/jsd/` script it injects into ordinary pages, is no longer taken for an anti-bot challenge (it was dropped and fetched again for ever, then refused): a challenge page is recognised by what it loads (`.../challenge-platform/h/...`), its title and its `_cf_chl_opt`.
- A series page lists only the albums of its own series: `Blake` no longer takes the albums of `Blake et Mortimer` (nor `Spirou` those of `Spirou et Fantasio`) from the "similar series" links, which made "link by series address" offer the wrong albums.
- Library upgrades: the copy kept before an upgrade (`<library>.bak-vN`) is written under a temporary name and renamed only when whole, and if it cannot be made (not enough room, a write error) **the upgrade is not done**, with a message; before, a full disk left a cut-short copy that was trusted for ever while the library was upgraded anyway. A copy left that way by an earlier version is set aside (`.unusable`) and made again. Compressing the stored pages skips a row that is not a page instead of crashing, and a `VACUUM` that has no room is reported, not fatal.
- `bdnex check --fix` no longer forgets every cover record and every trash record when their disk is not mounted (or the folder is mistyped): it keeps them, and says so, when the collection folder is missing or half or more of at least ten are gone, so `bdnex trash --restore` still works afterwards. It no longer deletes `*.part` files in your collection (a download of yours still going on): only bdnex's own `.bdnex-tmp` there, and `.part` in the covers folder. `check` is no longer quadratic when many files are missing.
- `bdnex reset -n` and `bdnex relocate -n` open the library read-only, so a preview can no longer back up, upgrade or rewrite paths in it. `relocate` of an import source says so (and changes nothing) when the new folder does not exist or is another source's folder.
- A collection folder whose name starts with `@` no longer breaks `ls`/`check` ("source not registered") for a library that stored its paths absolute (the path is written `./@...`, as everywhere else); a library path containing `?`, `#` or `%` opens read-only, and a read-only open waits for a busy file for 30 s instead of 5.
- A file name that says its volume (`T01`, `Tome 01`, `Vol. 3`) is read as that volume even when a bare number comes before it: `Apocalypse Mania - CYCLE 2 - Tome 01 - ...` is volume 1 (it was read as 2, the `CYCLE 2`), in the match, in the review screen and in series attach. A name without such a marker is read as before.
- Review screen: `a` with one file and one album picked links that album, exactly (it used to look up "the volume of the file in the album's series" and could offer another album, `Tome 2 - Experiment IV`, for the one you picked). Picking a **SERIES**, or marking several files, still gives each file its volume.
- `api.allowed_hosts`: the names the server answers to (see above).
- Review screen: `t` shows the few albums the current file could be (the list `bdnex candidates` prints: score, year, publisher, what the evidence says, and where the score comes from), and Enter links the highlighted one. It is quick, unlike `o`'s search of the whole album list, which is one `o` away from there.
- API: `GET /api/review/batch` returns the next files to check as `bdnex review --batch` shows them (doubtful, `unmatched`, `all`, `again`, a query, a size), and `POST /api/review/{file_id}/decide` keeps, unlinks or leaves any file of a batch, flagged or not, and marks it checked.
- Review screen: `b` goes back to the previous batch (with its decisions), and `n` comes forward again.
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
- **Series index** (schema v12, **back up the database first**: an older bdnex refuses it): `bdnex init` also reads the 27 letter pages of the site (`bandes_dessinees_0.html` ... `_z`), every series by name, into a new `series_index` table; `--skip-series` / `--series-only`. The review screen's search for a series (`a`) and `bdnex ls --sitemap WORDS` search it besides the albums. Series pages are still read on demand only.
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

## 0.10.0

- `bdnex komga show|push`: send the library's metadata (credits, summary, genres, series status and total...) to a Komga
  server through its API (Komga ignores the tags inside a PDF).
- The import fetches album pages while it matches, and builds each album as its pages arrive (`bdnex reparse` builds
  them offline from stored pages).
- A file's own `Web` address (CBZ, CBR or an attachment of a PDF) names its album when that album is in the album list.
- In-place repair keeps the damaged original in the trash; `bdnex completion` and the docs follow the commands.
