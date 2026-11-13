---
name: pr-checklist
description: Run the bdnex checks and publish a change as a PR (tests, py3.10 compile, Sphinx -W, review the staged diff, explicit git add, PR against main). Use when asked to commit, push or open a PR in this repo.
---

1. `git status`; the user's `TODO.md` stays untracked.
2. `uv run pytest -q` (all green; `test/test_docs_in_sync.py` fails when docs drift).
3. `uv run ruff check --select F bdnex` (undefined names) and `uv run python -m compileall -q bdnex`.
4. `uv run sphinx-build -W -E -q docs "$SCRATCH/html"` (warnings are errors).
5. Read `git diff` for anything private (the user's fetcher address, paths of their library) before staging.
6. Branch from `origin/main` (never commit on `main`, never stack on an unmerged branch: it would be merged into that
   branch, not into `main`), `git add <explicit paths>`, commit with the session's attribution trailers.
7. `git push -u origin <branch>`, then `gh pr create --base main` with the session's PR trailer. Say in the body what
   was *not* verified (terminal output, real data) and which earlier PR a stacked branch depends on.
8. Give the user the PR link; they merge.
