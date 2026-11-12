---
name: add-cli-option
description: Add or change a bdnex command, flag, config option, query field or database table without breaking the docs-sync test. Use when touching argparse parsers, bdnex/conf/bdnex.yaml, config.py, schema.py or ls fields.
---

`test/test_docs_in_sync.py` fails unless the docs say what the code does.

- **Flag**: add it in the command's parser in `bdnex/ui/<cmd>_cmd.py`, then mention `--flag` in a `docs/guides/*.md`
  page (importing.md for `import`). Completion scripts follow automatically (read from `--help`).
- **New command**: dispatch in `bdnex/ui/__init__.py` (+ a line in `USAGE`), add it to `COMMANDS` in
  `test/test_docs_in_sync.py`, and write "bdnex <command>" in the docs.
- **Config option**: `bdnex/conf/bdnex.yaml` (default), the pydantic model in `bdnex/config.py`, and
  `docs/guides/configuration.md` (the dotted name, e.g. `import.thumbnails`). Give the option a `# comment` in
  `bdnex/conf/bdnex.yaml`: that line is what `bdnex config --update` copies, commented out, into users' files
  (`test/test_config_template.py` checks the packaged file and the model list the same options). Bump `CONFIG_VERSION` only for removals.
- **Query field / table / migration**: `bdnex/library/schema.py` (new migration, never edit an old one), `query.py`
  fields, and the docs (`queries.md`, `maintaining.md`).
- Run `uv run pytest -q test/test_docs_in_sync.py` until green.
