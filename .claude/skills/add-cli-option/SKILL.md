---
name: add-cli-option
description: Add or change a bdnex command, flag, config option, query field or database table without breaking the docs-sync test. Use when touching argparse parsers, bdnex/conf/bdnex.yaml, config.py, schema.py or ls fields.
---

`test/test_docs_in_sync.py` fails unless the docs say what the code does.

- **Flag**: add it in the command's parser in `bdnex/ui/<cmd>_cmd.py`, then mention `--flag` in a `docs/guides/*.md`
  page (importing.md for `import`). Completion scripts follow automatically (read from `--help`).
- **New command**: a row in `bdnex/ui/commands.py` (name, module, summary, group; the usage text, dispatch and tab completion read
  it), its `bdnex/ui/<cmd>_cmd.py` with `run(argv)` (start with `ui/context.py`), rows in `bdnex/ui/examples.py`, `bdnex <command>`
  in `docs/guides/*.md`, and a name in `COMMANDS` of `test/test_docs_in_sync.py`.
- **Any option change**: regenerate the reference, `uv run python -m bdnex.ui.cli_reference > docs/reference/commands.md`
  (a test fails when it is stale). An example in `examples.py` that uses a removed flag fails `test/test_examples.py`.
- **A web task** that runs the command: its row in `jobs.SPECS`, `test/api/test_jobs_match_the_cli.py`, `docs/guides/api.md`.
- **Config option**: `bdnex/conf/bdnex.yaml` (default), the pydantic model in `bdnex/config.py`, and
  `docs/guides/configuration.md` (the dotted name, e.g. `import.thumbnails`). Give the option a `# comment` in
  `bdnex/conf/bdnex.yaml`: that line is what `bdnex config --update` copies, commented out, into users' files
  (`test/test_config_template.py` checks the packaged file and the model list the same options). Bump `CONFIG_VERSION` only for removals.
- **Query field / table / migration**: `bdnex/library/schema.py` (new migration, never edit an old one), `query.py`
  fields, and the docs (`queries.md`, `maintaining.md`).
- Run `uv run pytest -q test/test_docs_in_sync.py` until green.

A path-like option is named for what it holds (`collection_path`, `cover.dir`); a renamed option goes in
`config.RENAMED_KEYS` (the old name keeps working with a warning and `bdnex config --update` renames it in the file).
