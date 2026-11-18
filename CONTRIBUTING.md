# Contributing to bdnex

Thanks for helping. A few things that make a change easy to review:

- **Run the checks** (see `CLAUDE.md`, section *Checks before a PR*): `uv run pytest -q`, `uv run ruff check --select F bdnex`,
  `uv run python -m compileall -q bdnex` and `uv run sphinx-build -W -E -q docs <folder>`.
- **The command reference is generated.** After changing an option, refresh it:
  `uv run python -m bdnex.ui.cli_reference > docs/reference/commands.md` (a test fails when it is stale).
- **Docs are tested.** Every command, option, config key and query field must appear in `docs/`
  (`test/test_docs_in_sync.py` says what is missing). A new command is a row in `bdnex/ui/commands.py`.
- **Measure matching changes.** `tools/eval_matching.py run|report` re-runs the matcher on a library and reports how
  often a strong match is wrong. A matching change that is not measured on real data will not be merged: a wrong
  match costs more than a question.
- **Never commit private data**: a fetcher address, a library, comics. Tests never touch your home folder.
- Pull requests target `main`; one change per pull request; if two changes touch the same code, put them in one.

## AI-assisted changes

Much of bdnex was written with an AI coding assistant, under review by its maintainer. If you use one: say so in the pull
request and how far it went, read and understand every line you submit, keep comments short and about *why*, and test
what it wrote. Do not let an agent open pull requests or issues in your name.
