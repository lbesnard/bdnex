# bdnex

A beets-like library manager for French comics (Bédéthèque): SQLite library, `import` (match + fetch), `ls`,
`write`/`move`/`repair`/`convert`, `edit`, `reset`, `relocate`, `relink`, `review`, `undo`, `check`, `duplicates`, `trash`,
`series`, `komga` (pushes metadata to a Komga server through its API; Komga ignores the tags inside a PDF), `serve`,
`completion`; `import --as-is` records files first, `--select` re-imports by query. Docs: Sphinx in `docs/`
(Read the Docs). Python via **uv**; run things with `uv run`.

## Layout
- `bdnex/ui/*_cmd.py`: one module per command, each with a `run(argv)`. **`bdnex/ui/commands.py` is the one table of commands**
  (name, module, summary, group): the usage text and the dispatch read it; the tab completion reads the usage text. A new command =
  a row there, its module, its docs page entry (the sync tests say what is missing).
- `bdnex/ui/prompts.py` (match question, keys, Back, search, URL), `thumbnail.py`, `color.py`.
- `bdnex/importer/`: `matching.py` (fuzzy), `folders.py` (series/versions/unclear), `pipeline.py` (background matching),
  `session.py` (the import loop; its early page fetching is the `EarlyFetch` mixin in `session_early.py`, the cover and
  match-weighing pass is `CoverPass` in `session_covers.py`, the result types and exceptions are in `session_types.py`
  and re-exported by `session`), `covers.py`, `bedetheque/` (source plugin).
- `bdnex/organizer/`: `organizer.py` (write/move/repair: `process` is a few small steps, keep it that way),
  `archive.py` (lossless CBZ/CBR/PDF reading and writing), `comicinfo.py`, `template.py`; `bdnex/trash.py`, `versions.py`.
- `bdnex/komga.py` + `bdnex/ui/komga_cmd.py`: `bdnex komga show|push` (see "Komga" below).
- `bdnex/library/`: `database.py`, `schema.py` (migrations), `query.py` (the query language), `scan.py`.
- `bdnex/api/`: `bdnex serve`, a read-only FastAPI app plus one static page (`static/index.html`); extra `bdnex[api]`.
- `bdnex/config.py` + `bdnex/conf/bdnex.yaml` (every option and its default, also the source of the commented
  user file: `config_template.py`).

## The collection and its sources (a core idea: keep it straight)
- **One collection**: `collection_path` (was `directory`; the old key still works with a warning). bdnex organises it
  with the `paths` templates; the library tracks the files in it with **relative** paths.
- **Sources** (`import.sources`: `path`, `mode` copy|move, `name`) are where new files arrive. A file under one is
  stored as `@alias/relative` (table `roots` holds the real folder once, so `bdnex relocate OLD NEW` is one UPDATE);
  any other outside file keeps its absolute path. Always go through `Library._store` / `_resolve`, never build stored
  paths by hand. A collection folder whose name starts with `@` is stored as `./@…`.
- **A `copy` source is read-only**: `Organizer._read_only` refuses write/repair/move on files under it (only
  `COPY` is allowed). Never add a code path that modifies, moves, converts or deletes such a file.
- **Copies remember their origin** (`origins` table: source path, size, mtime): `import` skips what was already copied
  (`already copied to …`) and reports, but never re-copies, a file that changed at the source.
- `bdnex import .` / `--sources`: a path under a source follows that source's mode with no question; other outside
  folders follow `import.outside`; `--move` / `--copy` / `--no-move` win for one run. `import --as-is` + `--select` +
  `move` is how an existing collection is brought in.

## The web page (`bdnex/api/`)
- A FastAPI app + static files (`static/index.html`, `app.css`, `app.js`: plain JS, **no build step**, shipped in the
  wheel; everything from the server goes through `textContent`, never `innerHTML`). Reading opens the library
  read-only; **changing is off unless `api.allow_changes`** and then needs the token, the `X-Requested-With: bdnex`
  header and a same-site `Origin` (`can_change` in `app.py`).
- Tasks are `jobs.SPECS`: a fixed list that runs the CLI `run(argv, out=...)` functions in a thread, one at a time, never an
  arbitrary command line. Options are checked (`query` = plain terms, `paths` inside the collection or a source). A real
  run adds `--force` (nobody can answer questions yet); previews use `-n`. New web feature that writes = new spec + tests.
- **Questions on the page**: `api/webprompter.py` is `InquirerPrompter` with its four low-level prompts (`_select`, `_fuzzy`,
  `_text`, `_checkbox`) replaced: they put a question on the `Job` (`Job.ask` blocks the job's thread, `POST /api/jobs/{id}/answer`
  answers). Commands build their prompter with `prompts.new_prompter(...)`, never `InquirerPrompter(...)`, so a job can
  swap it (`use_prompter`). A new question must go through those four methods (not `inquirer.*` directly), and keep
  working with `fragments=None`. Never add a second copy of a question for the web.
- **Login** (`api/auth.py`): a form + session cookie (HttpOnly, SameSite=Strict) for browsers, HTTP Basic and the bearer token for
  scripts, `Throttle` (5 wrong guesses lock a client for 5 min; `api.behind_proxy` decides what "client" is). The page's own
  requests carry `X-Bdnex-Page` so a 401 never triggers the browser's Basic box. The CSP forbids inline scripts: no
  `<script>` bodies and no `onclick=` attributes in the static HTML.
- **Settings page** (`api/settings_api.py`): the form is built from the packaged defaults (their order and help) and the
  pydantic model (kinds, choices, limits); a change goes through `config_template.set_value` (line edit, comments kept) and
  `build_settings` before the file is written. A new option needs no page work, but a secret goes in `HIDDEN`.
- **Folder watch** (`api/watch.py`): a daemon thread of `bdnex serve` (started by the app's startup event, never by a test
  client) that, when `watch.enabled`, starts the `watch` task = `import --sources --unattended`. `--unattended` gives the session
  *no prompter* (strong matches applied, the rest skipped, duplicates skipped) and the organizer `force`: nothing is ever asked.
  Anything that must ask must not run from the watcher.
- **The metadata editor** (`POST /api/albums/{id}/edit`, `POST /api/edit`) calls `edit_cmd.doc_changes` / `describe_changes` /
  `parse_assignments`, so the web and `bdnex edit` can never disagree on what a valid value is. Do not validate fields twice.
- **Every API route and task is in `docs/guides/api.md`** (`test_docs_in_sync` fails otherwise), and `test/api/test_jobs_match_the_cli.py`
  checks every flag a task builds against its command's `--help`: a new task is a row in `COMMAND_OF` / `EVERYTHING` there. A
  real change goes through `can_change` (it marks the request; `protect` then writes `<library>.changes.jsonl`, `GET /api/history`;
  an endpoint may add `request.state.detail`). A command's logic is a function the CLI and the API both call
  (`candidates_cmd.build_report`, `stats_cmd.summary`, `mark_cmd.apply_marks`, `review_cmd.settle`): never a second copy.
- **Look at it for real**: Chromium is available. `tools/demo_library.py` builds a demo library; see the
  `check-web-page` skill for serving it and taking screenshots. Never claim a page works from the code alone.

## Komga (`bdnex komga show|push`, docs/guides/komga.md)
- Komga ignores the tags inside a PDF, so the library's metadata is pushed through its API (`POST /api/v1/books/list`,
  `PATCH /api/v1/books/{id}/metadata`, `PATCH /api/v1/series/{id}/metadata`; checked against Komga's OpenAPI file, not a live server).
- A library file is paired with a Komga book by **file name + size** (`komga.match`), never by path (containers, other
  machines); ties are left alone, never guessed. `show` only reads (a table: Komga value, bdnex value, what push would do);
  `push` sends **only what differs**, locks the fields it sends (`komga.lock`), and `--fields` limits it. Omitted fields are
  never sent (Komga leaves them alone), and tags are the genres *added* to Komga's own.
- A new field = a row in `komga.book_fields` / `series_fields`, its lock in `BOOK_LOCKS` / `SERIES_LOCKS`, its name in
  `komga_cmd.BOOK_FIELD_KEYS` / `SERIES_FIELD_KEYS`, the table in docs/guides/komga.md and a test with the fake session.
- `komga_cmd` parses with `parse_intermixed_args`: before Python 3.13 `argparse` rejects options after a positional list.

## The import fetches while it matches (`Bedetheque.page_queue`, `ImportSession._handle_arrivals`)
- A linked file's album page, then its series page, is downloaded in worker threads at once (`PageQueue`); **only the main
  thread touches SQLite** (`store()` / `wait()` return the arrivals). `_handle_arrivals` builds the album record from the
  stored pages (no request) and fills the file's result; covers wait for the cover pass (a request would block matching).
- A failed page is left for the sequential fetch phase, which reports it for its file. `--force` (recache) and `-n` do not
  use the queue. Ctrl-C / abort run `_finish_stored` (complete what is stored, then close the queue); never leave the
  executor open (its threads would hold the exit).
- Fetching writes to the library only: `--database-only` must still change no comic file (`test/test_outside.py`).

## A second look at a match (`importer/confirm.py`, `bdnex check --matches`)
- `confirm.assess(path, album, embedded)` weighs a file against its *complete* album (credits, year, volume, the file's own
  tags): pure, offline, no file read. Strong against = another volume, a "supplément" for a tome, the file's tags naming
  another series; weak = another year (a reissue), unknown names in a `(group)`. The **volume agreeing confirms nothing**
  (the match was ranked on it). A match that looks wrong is flagged (`files.review`), never unlinked.
- It runs when a page arrives (`ImportSession._confirm`) and for the whole library with `bdnex check --matches` (`library/health.doubtful_matches`). `confirm.judge` is the one place that decides; never re-implement it.
  Credit labels of a page are mapped in `parser.OTHER_CREDIT_ROLES` (`Encrage` is `Inker`); `LEGACY_ROLES` reads old names back.

- **`--auto=all` never asks and applies only strong matches without a doubt** (`match.auto_all`); a medium one is queued and
  decided at the end by its cover (`importer/session_vote.py`, `match.cover_vote`): never apply a medium match unasked.
- **One command for doubtful matches: `bdnex review`** (`--auto` settles by rule). `library/recheck.py` weighs the flagged files and
  the matches applied under `match.strong_threshold` (page, tags, saved covers); `ui/verdicts.py` looks for a better album by the covers of
  the other candidates (`look_for_better`, the cover-vote rule) and applies the verdicts (`settle_verdicts`: one place): kept = flag goes,
  wrong = switched or unlinked, unconfirmed = left alone. It never touches a file chosen by the user (no score in the import log).
  `recheck` is only a renamed `review --auto` (`commands.RENAMED`). **Aliases** (`aliases: ["fix = review --auto"]`, `commands.expand_alias`)
  are a shorter way to type a bdnex command, nothing else: never let one run a shell command.
- **Precision before coverage**: a wrong match costs more than an unasked one. A strong first choice is still asked about
  when a *doubt* is set (`Match.doubt`, `matching._doubts`): another album within `match.min_margin` points, or a strong
  contradiction. Candidates whose page was read get a few points when year/author agree and **never lose any** (a penalty
  biases against known albums; measured). Change the matcher only with `tools/eval_matching.py run/report` on a real
  library (answer key = matches `confirm.assess` calls confirmed); look at wrong-at-strong, not just totals.

## Several files at once (`Organizer.run(workers=)`, `bdnex/parallel.py`)
- `parallel.map_ordered(function, items, workers)` is the helper for read-only work (hashing, reading first pages): forked processes,
  results in order, an exception is the item's result. It must not touch a database; the caller stores the results.
- `write`, `move` and `repair` use `Organizer.run(workers=, repair=)`; `duplicates --covers`, `relink` and `import --as-is`
  use `map_ordered`.
- Workers are forked processes with their own `Library` connection (a connection is never shared; an in-memory library
  can not be used with workers). A worker holds a `_Deferring` prompter: any question raises `Deferred` **before anything is
  written**, and the main process redoes that file with the real prompter, in order. So a question must always be asked
  *before* the new file is built (see `_delete_wanted`); never add one after `_build`.
- `_independent` keeps files with the same destination (or a destination another file is leaving) out of the pool.
  Progress for the bar is read from shared memory (`_STATE`): `Future.running()` is true as soon as a task is queued.
- Memory: a file is held whole while it is worked on; a PDF can be hundreds of MB. `write.workers` caps it.

## Docker (`Dockerfile`, `compose.yaml`, `docker/`, docs/guides/docker.md)
- The image runs `bdnex serve` as `PUID:PGID` (compose `user:`), `HOME` is a writable temp folder; config, library and
  covers are volumes (`/config`, `/data`, `/covers`) beside `/comics`. The entrypoint seeds `/config/bdnex.yaml` and an empty
  library, so `serve` can start. The login is HTTP Basic (`api.username` / `BDNEX_PASSWORD`); `/healthz` is the only open route.
- A change to the image is tested for real: `docker build -t bdnex:local .`, run it with temp volumes, curl `/healthz`
  and a login. Never put a user's fetcher address or password in the image or the repo (`.env` is ignored).

## Rules that are easy to forget
- **Tests never touch the real home.** `test/conftest.py` has an autouse `isolated_home` fixture; keep it.
- **Docs are enforced by tests** (`test/test_docs_in_sync.py`): every command, flag, config option, query field and
  table must appear in `docs/`. Add the docs in the same change (see the `add-cli-option` skill).
- **Never commit the user's private fetcher address** (it lives in their own `~/.config/bdnex/bdnex.yaml`). Check
  `git diff --cached` before every commit, and never `git add -A` / `git add .`: add paths explicitly. `TODO.md` is
  the user's, untracked, never committed.
- `flaresolverr_fetch` (`bedetheque.fetcher`, `fetcher_endpoint`) is the default page fetcher and the user's own code:
  do not change what it does or run it against the site, and never put their endpoint address in the repo. The config
  validates that `bedetheque.fetcher` imports and is callable. `fetcher_endpoint` is a list (a string is one item): used
  round-robin with fallback, and pages are fetched `parallel` at a time (`Bedetheque.page_queue` / `PageQueue`: network in worker
  threads started as soon as a file is matched, album then series page, SQLite only in the calling thread; the album record is built in the loop as the pages arrive, `ImportSession._handle_arrivals`; `prefetch` is the older, ordered form). Covers do not use it (plain request).
- Do not modify the user's real database or comics. Work on copies in the session scratchpad.
- PRs target `main`. **Do not stack**: a PR based on another branch is merged into that branch, not into `main`, and its
  work never ships (it happened twice). Branch from `origin/main`; if two changes touch the same code, merge the first
  one into the second branch, or put both in one PR.
- **A schema change appends a migration** in `schema.py` (never edit a released one), documents the column in
  `docs/dev/library.md`, and the PR says "back up the database first": an older bdnex refuses a newer schema.
- **Dependencies:** images are read and drawn with PyMuPDF only (no PIL, no numpy). OpenCV is a normal dependency (the `cover`
  extra is empty, kept for old install commands) but still degrades: `covers.opencv_installed()`. Anything optional must degrade, never crash.
- **`-n` (preview) is read-only and fast**: it opens the library read-only and never reads page data.
- **Covers are fetched with a plain request**, never with the user's page fetcher, and are validated as pictures
  before they are kept.
- Commit and PR trailers come from the session's attribution reminder; do not hard-code a session URL here.
- Terminal pictures (kitty, iTerm2, tmux) cannot be verified without the user's terminal: say so honestly.

## Checks before a PR
`uv run pytest -q`, `uv run ruff check --select F bdnex` (catches undefined names the tests miss: a prompt that
never ran in a test crashed that way), `uv run python -m compileall -q bdnex` (Python >= 3.10 syntax), and
`uv run sphinx-build -W -E -q docs <scratch>/html`. See the `pr-checklist` skill.

## Conventions
- Comments say *why*; keep docs and code in plain, short English. French only for album data.
- Prompts: InquirerPy is wrapped by thin `_select/_text/_fuzzy/_checkbox` methods so tests can script them. **Keys,
  labels and colours live in one table, `bdnex/ui/keys.py` (`ACTIONS`)**: a question lists the actions it offers
  (`_pick` / `_ask` entries `{"action": "skip", "value": ...}`, `label=` to reword), never hand-written `[K]` text or
  colours. `skip` is always `S`, `skip_folder` `F`, `back` `B`, `abort` `A` (red, last); green = safe, yellow = put
  off, red = destructive or stop. A new answer = a new row there (and the keys table in `docs/guides/importing.md`);
  `build` raises when a question uses a key twice. Write-side questions end with `[A]bort` (`_ask` adds it).
- Tests: autouse fixtures isolate HOME and switch the automatic cover check off; `FakeWeb` (test/helpers.py) serves
  fixtures and a real picture. A bug found by reading code gets a test that fails without the fix.
- **Verbosity** (`ui/output.py`, `ImportSession.verbosity`): default = phases, a count line every percent, and only the files that need a look
  (skipped with *why*, duplicate, failed); `-v` = every file + reason (`session.detail()`); `-vv` = bdnex's own debug (`logger.debug`,
  thread names); `-vvv` = libraries + SQL (`bdnex.sql`); `--log-file` always gets debug. A new per-file message goes through
  `detail()`, a decision that needs the user's eye goes through `_attention`, and a skip must carry its reason in `result.note`.
- Long commands show their phases with `banner()` and tint progress lines (`ui/output.py`); plain text in a pipe.
- **Shared options and formats**: `-n` is `add_pretend(parser, "what would happen")` and `--workers` is
  `add_workers(parser, ...)` (`ui/output.py`); sizes, durations and short paths are `bdnex/formatting.py`
  (`human_size`, `human_duration`, `short_path`). Do not write another `megabytes`.
- **A command starts with `ui/context.py`**: `settings = context.settings(...)` (config + the library file exists, one
  wording, exit code 1), `context.has_library(settings, "nothing to ...")`, `context.bad_query(err)` (exit code 2).
  Never write your own "No library yet" or `except ConfigError`.
- **Results are drawn by `ui/results.py`**: one table of outcome -> colour (`STYLES`), one shape (`head` / `block` /
  `footer`: `● label  Title`, indented lines, a rule and counts). A command names the outcome; it never picks a colour
  or prints its own `●`. A new kind of result = a row in `STYLES`.
- Anything drawn is a help, never a reason for the import to fail (swallow errors, log at debug).
