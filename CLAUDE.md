# bdnex

A beets-like library manager for French comics (Bédéthèque): SQLite library, `import` (match + fetch), `ls`, `write`/`move`/
`repair`/`convert`, `edit`, `review`, `check`, `komga`, `serve` (web page + JSON API) and more (`bdnex --help`; the table of
commands is `bdnex/ui/commands.py`). Python 3.10-3.13, run with **uv** (`uv run ...`). Docs: Sphinx in `docs/` (Read the Docs).
About 1900 tests; the whole suite takes ~6 minutes, so run the tests of what you touched while working and everything before a PR.

## Layout
- `bdnex/ui/*_cmd.py`: one module per command, each with a `run(argv)`. **`ui/commands.py` is the one table of commands**
  (name, module, summary, group): the usage text, the dispatch and the tab completion read it. `RENAMED` holds old names that
  became options of another command (`recheck`, `reparse`, `relink`, `fields`, `mirror`). Shared UI: `prompts.py`, `keys.py`,
  `color.py`, `results.py`, `context.py`, `output.py`, `examples.py` (the examples at the end of every `--help`).
- `bdnex/importer/`: `matching.py` (fuzzy), `folders.py` (series/versions/unclear), `pipeline.py`, `session.py` (the import loop,
  split in `session_early.py`, `session_covers.py`, `session_vote.py`, `session_types.py`), `confirm.py`, `covers.py`,
  `candidates.py`, `album_search.py`, `mirror.py`, `source.py` + `registry.py` (the source plugin contract,
  `docs/dev/plugins.md`), `bedetheque/` (the built-in source).
- `bdnex/organizer/`: `organizer.py` (write/move/repair; `process` is a few small steps, keep it that way), `archive.py`
  (lossless CBZ/CBR/PDF), `comicinfo.py`, `template.py`. Also `bdnex/trash.py`, `versions.py`, `komga.py`, `parallel.py`.
- `bdnex/library/`: `database.py`, `schema.py` (migrations), `query.py` (query language), `scan.py`, `health.py`, `recheck.py`,
  `review_batch.py`. `bdnex/api/`: `bdnex serve`. `bdnex/config.py` + `bdnex/conf/bdnex.yaml` (every option and default; also
  the source of the commented user file, `config_template.py`).
- Generated or enforced: `docs/reference/commands.md` is generated (`uv run python -m bdnex.ui.cli_reference > docs/reference/commands.md`
  after any option change); `test_docs_in_sync` and `test_examples` fail when docs or examples drift from the code.

## The collection and its sources (keep it straight)
- **One collection**: `collection_path` (old key `directory` still works with a warning). bdnex organises it with the `paths`
  templates; the library tracks its files with **relative** paths.
- **Sources** (`import.sources`: `path`, `mode` copy|move, `name`) are where new files arrive. A file under one is stored as
  `@alias/relative` (table `roots` holds the real folder once, so `bdnex relocate OLD NEW` is one UPDATE); any other outside file keeps
  its absolute path. Always go through `Library._store` / `_resolve`, never build stored paths by hand. A collection folder whose name
  starts with `@` is stored as `./@…`.
- **A `copy` source is read-only**: `Organizer._read_only` refuses write/repair/move on files under it. Never add a code path that
  modifies, moves, converts or deletes such a file.
- **Copies remember their origin** (`origins` table): `import` skips what was already copied and never re-copies a file that changed at the source.
- `bdnex import .` / `--sources`: a path under a source follows its mode with no question; other outside folders follow `import.outside`;
  `--move` / `--copy` / `--no-move` win for one run.

## Safety rules (the ones that cost the most when forgotten)
- **Never touch the user's real library, comics or the real site from a session.** A bdnex of this code opens the real library
  read-write and *upgrades* it. Work on copies in the scratchpad. `flaresolverr_fetch` (`bdnex/fetchers.py`, config `fetch.*`) is the
  user's own code: never run it against the site, never change its behaviour or endpoint setup (a failure-handling bug may be fixed with a
  test that fakes `requests.post`), never put their endpoint address in the repo. `init --mirror` is never run for real either.
- **Tests never touch the real home** (autouse `isolated_home` in `test/conftest.py`; it also switches the automatic cover check off).
  `FakeWeb` (`test/helpers.py`) serves fixtures and a real picture. A bug found by reading code gets a test that fails without the fix.
- **Precision before coverage**: a wrong match costs more than an unasked one. `--auto=all` never asks and applies only strong matches
  without a *doubt* (`Match.doubt`, `matching._doubts`); a medium one is queued and decided by its cover (`session_vote`). Never apply a
  medium match unasked. A match that looks wrong is flagged (`files.review`), never unlinked by itself. Change the matcher only with
  `tools/eval_matching.py run/report` on a real library (look at wrong-at-strong, not totals); the cover comparison with
  `tools/eval_covers.py` (change `covers.sift_similarity` / `cover.match_percentage` only with its numbers).
- **`-n` (preview) is read-only and fast**: opens the library read-only, never reads page data. `import --database-only` changes no comic file.
- **A schema change appends a migration** in `schema.py` (never edit a released one), documents the column in `docs/dev/library.md`,
  and the PR says "back up the database first": an older bdnex refuses a newer schema.
- **Stored pages are compressed** (`Library.COMPRESSED_KINDS`): never read `source_cache.content` of an album/series/letter page directly,
  use `cache_get` / `cache_put`. A cover address of an album whose page was not read is a guess (`guess_cover_url`).
- **Covers are fetched with a plain request**, never the page fetcher, and validated as pictures before they are kept.
- **Never commit private data** (fetcher address, a library, comics, paths of the user's library). `TODO.md` is the user's: untracked,
  never committed. Never `git add -A` / `.`: add paths explicitly and read `git diff --cached` first.
- **Dependencies**: images are read and drawn with PyMuPDF only (no PIL, no numpy). OpenCV is a normal dependency but still degrades
  (`covers.opencv_installed()`). Anything optional must degrade, never crash. Anything drawn is a help, never a reason for an import to fail.

## Concurrency (import and organizer)
- **Import fetches while it matches** (`Bedetheque.page_queue` / `PageQueue`, `ImportSession._handle_arrivals`): album then series page
  are downloaded in worker threads; **only the main thread touches SQLite**. A failed page is left to the sequential phase. `--force` and
  `-n` skip the queue. Ctrl-C / abort run `_finish_stored`; never leave the executor open (its threads would hold the exit).
- **Several files at once** (`Organizer.run(workers=)`, `parallel.map_ordered`): workers are forked processes with their own `Library`
  connection (never shared; no in-memory library). `map_ordered` is for read-only work and must not touch a database. A worker holds a
  `_Deferring` prompter: a question raises `Deferred` **before anything is written** and the main process redoes the file. So a
  question must be asked *before* the new file is built (see `_delete_wanted`); never add one after `_build`. `_independent` keeps files
  with the same destination out of the pool. A file is held whole in memory (PDFs can be hundreds of MB): `write.workers` caps it.

## Match checking (`confirm`, `review`, `recheck`)
- `confirm.assess(path, album, embedded)` weighs a file against its *complete* album: pure, offline, no file read. `confirm.judge` is the one
  place that decides (never re-implement it). Strong against = another volume, a "supplément" for a tome, the file's tags naming another
  series; weak = another year, unknown names in a `(group)`. The volume agreeing confirms nothing (the match was ranked on it).
  It runs when a page arrives (`ImportSession._confirm`) and over the library with `bdnex check --matches`.
- **`bdnex review`** is the one command for doubtful matches (`--auto` settles by rule through `ui/verdicts.py`: `settle_verdicts` is the
  one place that applies verdicts; it never touches a file the user chose). `--batch N` is a Textual screen (`ui/review_tui.py`) over
  `library/review_batch.py`: keep the logic there and the screen thin; test it with Textual's pilot (`test/test_review_tui.py`). Nothing slow
  runs in the screen's thread (workers with their own read-only `Library`; the screen's own library writes the decisions; downloads go
  through `ui/review_fetch.DetailsQueue`). `bdnex ls --sitemap` / `--for FILE` and the screen's `o` are the same offline search
  (`album_search.search_folded`).
- **Going through the whole collection** (`review --batch`): `library/folders.py` (the tree: counts and colours from the `checked` mark, `inconsistent`,
  `mark_reviewed`), `library/bulk.py` (one form for many files: `shared` / `apply`, checks first through `edit_cmd.doc_changes`, nothing written when
  one value is refused), `library/twins.py` (files of one album: compare, trash, `make_main`, `keep_as_version`). The screen side is `ui/review_panels.py`
  (`FolderTree`, `DuplicateScreen`, `BulkScreen`) and the mixin `ui/review_extras.py`; keys `f R D E K V`. A hidden `Input` still takes the focus
  (disable it). `category` (`$category`) is a hand-set field apart from `kind`; `parent_id` + `relation` (`version` goes to the spare versions folder).
  The left panel (folders / after a move / filters) reads `library/browse.py`: `rows_for` (one indexed query per selection, 1000 rows at most; never
  weigh files one by one for a list), `plan` (where a move puts every file, ~0.1 ms each, in memory, updated from the albums that change), `facets`.
  The table's columns are a registry in `ui/review_columns.py` (`COLUMN_TYPES`, `ColumnsMixin`: a new field = a row there, a `cell` branch and a sort key); the
  view (columns, pane sizes, tab, `d` panel) is saved in `meta` under `review.view`. A mixin cannot use `@on`: name handlers `on_<message>`; a Textual app does
  not receive its widgets' clicks (subclass the widget, see `ReviewTable`). Colours: a file is green when *you* confirmed it (the `checked` mark, any value but `unlink`), else cyan auto matched / orange medium match / red doubtful or no album (`browse.level_sql`: the import's score against `match.strong_threshold`, `files.review`); a folder takes its worst unconfirmed file. Two columns, not one: **Link** (bdnex's: how it was linked) and **Review** (yours). An unlink never confirms.
  Schema v17 is indexes only. Long work runs in a worker with its own read-only `Library`.
  A series / one-shot choice by hand is `Library.assign_series` / `make_oneshot` and is stored in the album's edits so a re-import keeps it.
- Aliases (`aliases: ["NAME = COMMAND  # WHY"]`, `commands.expand_alias`) are a shorter way to type a bdnex command, nothing else: never
  let one run a shell command. Every command needs rows in `ui/examples.py`.

## The web page (`bdnex/api/`)
- FastAPI + plain static JS/CSS (`static/`, **no build step**, shipped in the wheel). Everything from the server goes through
  `textContent`, never `innerHTML`; the CSP forbids inline scripts (no `<script>` bodies, no `onclick=`).
- Reading opens the library read-only. **Changing is off unless `api.allow_changes`**, then needs the token, the `X-Requested-With: bdnex`
  header and a same-site `Origin` (`can_change` in `app.py`); a real change is logged (`protect` → `<library>.changes.jsonl`, `GET /api/history`).
- Tasks are `jobs.SPECS`: a fixed list that runs the CLI `run(argv, out=...)` functions in a thread, one at a time, never an arbitrary
  command line. A real run adds `--force`; previews use `-n`. **New web feature that writes = a new spec + tests**, a row in `COMMAND_OF` /
  `EVERYTHING` in `test/api/test_jobs_match_the_cli.py` (it checks every flag a task builds against the command's `--help`), and the route
  or task in `docs/guides/api.md`.
- A command's logic is a function the CLI and the API both call (`candidates.build_report`, `stats_cmd.summary`, `mark_cmd.apply_marks`,
  `review_cmd.settle`, `edit_cmd.doc_changes`/`describe_changes`/`parse_assignments`): never a second copy, never validate a field twice.
- **Questions on the page**: `api/webprompter.py` replaces the four low-level prompts (`_select`, `_fuzzy`, `_text`, `_checkbox`) so a job
  can wait for an answer. Commands build their prompter with `prompts.new_prompter(...)`, never `InquirerPrompter(...)`; a new question goes
  through those four methods, not `inquirer.*`, and works with `fragments=None`.
- **Login** (`auth.py`): form + session cookie for browsers, HTTP Basic / bearer token for scripts, `Throttle` after 5 wrong guesses. The
  page's own requests carry `X-Bdnex-Page` so a 401 never opens the browser's Basic box. **Settings page**: the form is built from the
  packaged defaults and the pydantic model; a change goes through `config_template.set_value`; a secret goes in `HIDDEN`.
- **Folder watch** (`watch.py`, started by the app's startup event, never by a test client) runs `import --sources --unattended`:
  no prompter, strong matches applied, the rest skipped. Anything that must ask must not run from the watcher.
- **Look at it for real** (see the `check-web-page` skill). Never claim a page works from the code alone.

## Komga (`bdnex komga show|push`, `docs/guides/komga.md`)
- Komga ignores the tags inside a PDF, so metadata is pushed through its API (checked against its OpenAPI file, not a live server).
- A library file is paired with a Komga book by **file name + size** (`komga.match`), never by path; ties are left alone. `show` only reads;
  `push` sends **only what differs**, locks the fields it sends, `--fields` limits it; omitted fields are never sent.
- A new field = a row in `komga.book_fields` / `series_fields`, its lock in `BOOK_LOCKS` / `SERIES_LOCKS`, its name in
  `komga_cmd.BOOK_FIELD_KEYS` / `SERIES_FIELD_KEYS`, the docs table and a test with the fake session.
- `komga_cmd` parses with `parse_intermixed_args` (before Python 3.13 `argparse` rejects options after a positional list).

## Docker (`Dockerfile`, `compose.yaml`, `docker/`, `docs/guides/docker.md`)
- Runs `bdnex serve` as `PUID:PGID`, `HOME` a writable temp folder; config, library, covers are volumes (`/config`, `/data`, `/covers`)
  beside `/comics`. The entrypoint seeds `/config/bdnex.yaml` and an empty library. Login is HTTP Basic (`api.username` / `BDNEX_PASSWORD`);
  `/healthz` is the only open route. A change to the image is tested for real (`docker build -t bdnex:local .`, run with temp volumes, curl
  `/healthz` and a login: CI does the same). Never put a user's fetcher address or password in the image or repo.

## Conventions
- Comments say *why*; docs and code in plain, short English. French only for album data. A bug found by reading gets a test.
- **Prompts**: InquirerPy is wrapped by thin `_select/_text/_fuzzy/_checkbox` methods so tests can script them. **Keys, labels and colours
  live in one table, `ui/keys.py` (`ACTIONS`)**: a question lists the actions it offers, never hand-written `[K]` text. `skip` is always `S`,
  `skip_folder` `F`, `back` `B`, `abort` `A` (red, last); a new answer = a new row there (and the keys table in `docs/guides/importing.md`);
  `build` raises when a question uses a key twice. Write-side questions end with `[A]bort`.
- **Colour is a role, never a colour name** (`ui/color.py`: `role(text, "bad", on)`, `verdict`, `hints`, `paint_score`), with `on = color.enabled(out)`:
  plain text in a pipe, and the text must be identical with and without colour. Log lines (`-v`) go through `color.LogFormatter`.
- **A command starts with `ui/context.py`**: `settings = context.settings(...)`, `context.has_library(settings, "nothing to ...")`,
  `context.bad_query(err)` (exit code 2). Never write your own "No library yet" or `except ConfigError`.
- **Results are drawn by `ui/results.py`**: a command names the outcome (a row in `STYLES`); it never picks a colour or prints its own `●`.
- **Shared options and formats**: `-n` is `add_pretend(parser, ...)`, `--workers` is `add_workers(parser, ...)` (`ui/output.py`); sizes,
  durations, short paths are `bdnex/formatting.py`. Do not write another `megabytes`.
- **Verbosity** (`ui/output.py`, `ImportSession.verbosity`): default = phases, a count line per percent and only the files that need a look;
  `-v` = every file (`session.detail()`); `-vv` = debug; `-vvv` = libraries + SQL; `--log-file` always gets debug. A new per-file message goes
  through `detail()`, a decision for the user's eye through `_attention`, and a skip carries its reason in `result.note`. Every step of a slow
  command is said as it happens. Long commands show phases with `banner()`.
- **Config**: a new option needs the default (with a `# comment`) in `bdnex/conf/bdnex.yaml`, the pydantic model in `config.py` and
  `docs/guides/configuration.md`; renamed keys go in `RENAMED_KEYS` / `MOVED_KEYS`; bump `CONFIG_VERSION` only for removals.
- Terminal pictures (kitty, iTerm2, tmux) cannot be verified without the user's terminal: say so honestly.

## Checks before a PR
`uv run pytest -q -n 4` (parallel: about 2.5 minutes instead of 6; CI does the same with `-n auto`), `uv run ruff check --select F bdnex` (catches undefined names the tests miss), `uv run python -m compileall -q bdnex`
(Python >= 3.10 syntax), and `uv run sphinx-build -W -E -q docs <scratch>/html` (needs `uv sync --extra docs`). CI also runs 3.10-3.13, the
docs and a Docker build. PRs target `main`; **do not stack** (a PR based on another branch is merged into that branch and never ships):
branch from `origin/main`, and if two changes touch the same code put them in one PR. Commit and PR trailers come from the session's
attribution reminder. See the `pr-checklist` skill.
