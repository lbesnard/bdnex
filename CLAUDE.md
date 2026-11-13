# bdnex

A beets-like library manager for French comics (Bédéthèque): SQLite library, `import` (match + fetch), `ls`,
`write`/`move`/`repair`, `edit`, `reparse`, `reset`, `relocate`, `relink`, `review`, `undo`, `check`, `duplicates`, `trash`,
`series`, `serve`,
`completion`; `import --as-is` records files first, `--select` re-imports by query. Docs: Sphinx in `docs/`
(Read the Docs). Python via **uv**; run things with `uv run`.

## Layout
- `bdnex/ui/*_cmd.py`: one module per command, each with a `run(argv)`; dispatch in `bdnex/ui/__init__.py`.
- `bdnex/ui/prompts.py` (match question, keys, Back, search, URL), `thumbnail.py`, `color.py`.
- `bdnex/importer/`: `matching.py` (fuzzy), `folders.py` (series/versions/unclear), `pipeline.py` (background matching),
  `session.py` (the import loop, Journal/undo), `covers.py`, `bedetheque/` (source plugin).
- `bdnex/organizer/`: `organizer.py` (write/move/repair: `process` is a few small steps, keep it that way),
  `archive.py` (lossless CBZ/CBR/PDF reading and writing), `comicinfo.py`, `template.py`; `bdnex/trash.py`, `versions.py`.
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
- **Look at it for real**: Chromium is available. `tools/demo_library.py` builds a demo library; see the
  `check-web-page` skill for serving it and taking screenshots. Never claim a page works from the code alone.

## Several files at once (`Organizer.run(workers=)`)
- Workers are forked processes with their own `Library` connection (a connection is never shared; an in-memory library
  can not be used with workers). A worker holds a `_Deferring` prompter: any question raises `Deferred` **before anything is
  written**, and the main process redoes that file with the real prompter, in order. So a question must always be asked
  *before* the new file is built (see `_delete_wanted`); never add one after `_build`.
- `_independent` keeps files with the same destination (or a destination another file is leaving) out of the pool.
  Progress for the bar is read from shared memory (`_STATE`): `Future.running()` is true as soon as a task is queued.
- Memory: a file is held whole while it is worked on; a PDF can be hundreds of MB. `write.workers` caps it.

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
  round-robin with fallback, and pages are fetched `parallel` at a time (`Bedetheque.prefetch`: network in worker
  threads, SQLite only in the calling thread). Covers do not use it (plain request).
- Do not modify the user's real database or comics. Work on copies in the session scratchpad.
- PRs target `main`. **Do not stack**: a PR based on another branch is merged into that branch, not into `main`, and its
  work never ships (it happened twice). Branch from `origin/main`; if two changes touch the same code, merge the first
  one into the second branch, or put both in one PR.
- **A schema change appends a migration** in `schema.py` (never edit a released one), documents the column in
  `docs/dev/library.md`, and the PR says "back up the database first": an older bdnex refuses a newer schema.
- **Dependencies:** images are read and drawn with PyMuPDF only (no PIL, no numpy). OpenCV is the optional `cover`
  extra. Anything optional must degrade, never crash.
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
- Prompts: InquirerPy is wrapped by thin `_select/_text/_fuzzy/_checkbox` methods so tests can script them. Every
  question lists its keys as `[K]ey` with colour, like the match question; write-side questions end with `[A]bort`.
- Tests: autouse fixtures isolate HOME and switch the automatic cover check off; `FakeWeb` (test/helpers.py) serves
  fixtures and a real picture. A bug found by reading code gets a test that fails without the fix.
- Long commands show their phases with `banner()` and tint progress lines (`ui/output.py`); plain text in a pipe.
- Anything drawn is a help, never a reason for the import to fail (swallow errors, log at debug).
