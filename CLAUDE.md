# bdnex

A beets-like library manager for French comics (Bédéthèque): SQLite library, `import` (match + fetch), `ls`,
`write`/`move`, `edit`, `reparse`, `reset`, `relocate`, `trash`, `series`, `completion`. Docs: Sphinx in `docs/`
(Read the Docs). Python via **uv**; run things with `uv run`.

## Layout
- `bdnex/ui/*_cmd.py`: one module per command, each with a `run(argv)`; dispatch in `bdnex/ui/__init__.py`.
- `bdnex/ui/prompts.py` (match question, keys, Back, search, URL), `thumbnail.py`, `color.py`.
- `bdnex/importer/`: `matching.py` (fuzzy), `folders.py` (series/versions/unclear), `pipeline.py` (background matching),
  `session.py` (the import loop, Journal/undo), `covers.py`, `bedetheque/` (source plugin).
- `bdnex/library/`: `database.py`, `schema.py` (migrations), `query.py`, `scan.py`.
- `bdnex/config.py` + `bdnex/conf/bdnex.yaml` (every option and its default).

## Rules that are easy to forget
- **Tests never touch the real home.** `test/conftest.py` has an autouse `isolated_home` fixture; keep it.
- **Docs are enforced by tests** (`test/test_docs_in_sync.py`): every command, flag, config option, query field and
  table must appear in `docs/`. Add the docs in the same change (see the `add-cli-option` skill).
- **Never commit the user's private fetcher address** (it lives in their own `~/.config/bdnex/bdnex.yaml`). Check
  `git diff --cached` before every commit, and never `git add -A` / `git add .`: add paths explicitly. `TODO.md` is
  the user's, untracked, never committed.
- The page fetcher for the anti-bot protection is the user's own function (`bedetheque.fetcher`,
  `fetcher_endpoint`). Do not write, run or document it; the README only says a user-supplied fetcher is needed.
- Do not modify the user's real database or comics. Work on copies in the session scratchpad.
- PRs always target `main`. A stacked branch contains its predecessor's commits; say so in the PR body.
- Commit and PR trailers come from the session's attribution reminder; do not hard-code a session URL here.
- Terminal pictures (kitty, iTerm2, tmux) cannot be verified without the user's terminal: say so honestly.

## Checks before a PR
`uv run pytest -q`, `uv run python -m compileall -q bdnex` (Python >= 3.10 syntax), and
`uv run sphinx-build -W -E -q docs <scratch>/html`. See the `pr-checklist` skill.

## Conventions
- Comments say *why*; keep docs and code in plain, short English. French only for album data.
- Prompts: InquirerPy is wrapped by thin `_select/_text/_fuzzy` methods so tests can script them.
- Anything drawn is a help, never a reason for the import to fail (swallow errors, log at debug).
