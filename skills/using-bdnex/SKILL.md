---
name: using-bdnex
description: Operate bdnex, the library manager for French comics (Bédéthèque), safely on someone's collection. Use when the user asks to import, match, tag, rename, convert, query or push to Komga their comics with bdnex, or asks how bdnex works.
---

bdnex keeps a SQLite library of your comic files and the Bédéthèque albums they match. Commands: `import`, `ls`,
`write`, `move`, `repair`, `convert`, `edit`, `reparse`, `series`, `check`, `duplicates`, `undo`, `relink`, `relocate`,
`reset`, `trash`, `komga`, `serve`, `config`. Run it as `bdnex <command>` (from a clone: `uv run bdnex <command>`).
`bdnex <command> --help` is exact; the guides (docs/guides, or the Read the Docs site) explain the workflows.

## Rules that protect a collection

1. **Preview first.** `-n` (`--pretend`) shows what would happen and changes nothing. Run it, show the user, then run for real.
2. **A first import must not touch files**: `bdnex import --database-only <folder>` only fills the library. `--auto=all`
   asks nothing (strong and medium matches are applied, the rest skipped); drop it to check each match yourself.
3. **Only `write`, `move`, `repair` and `convert` change comic files** (and `import` with `-W`, `--move` or `--copy`).
   They can convert CBR/PDF to CBZ and rename files: say so, and offer `-n` and a backup of the collection first.
   `bdnex undo` takes back a `move`. A source with mode `copy` is read-only and is never modified.
4. **The library is a file** (`library:` in the configuration, `bdnex config` shows it). Copy it before anything that
   rewrites many records (`reset` makes its own backup; an older bdnex refuses a newer library).
5. **Never put the user's page fetcher address, passwords or API keys** in a command line you print, a commit or a
   shared file: they live in the user's configuration (`bdnex config --edit`).
6. Matches can be wrong: a file named like one series may match another. Look at the list (`bdnex ls --long`,
   `bdnex ls --unmatched`) before pushing the data anywhere.

## Common jobs

| Goal | Command |
|---|---|
| bring a folder in without touching files | `bdnex import --database-only --auto=all <folder>` (`-v` every file, `-vv` how, `--log-file import.log`) |
| fetch the details of albums that only have a link | `bdnex import --fetch --select partial:true` |
| what is in the library | `bdnex ls series:nains`, `bdnex ls complete:true -c`, `bdnex ls --unmatched` |
| a missing summary or credits | `bdnex reparse -n` (offline, from stored pages), then `bdnex edit` for a hand fix |
| write tags into files | `bdnex write -n`, then `bdnex write` |
| organise into the folder tree | `bdnex move -n`, then `bdnex move` (`bdnex undo` to take it back) |
| find duplicates, broken archives | `bdnex duplicates`, `bdnex check`, `bdnex repair -n` |
| a Komga server (it ignores tags inside a PDF) | `bdnex komga show format:pdf`, then `bdnex komga push -n`, then `push` (`--fields` to send only some) |
| browse in a browser | `bdnex serve` |

An import can be stopped with Ctrl-C and run again: what was decided is kept, and `bdnex import --fetch` continues the
page downloads. Pages are requested politely (`bedetheque.delay`, `bedetheque.parallel`); a large collection takes hours.

## Queries (`ls`, `write`, `move`, `komga` ...)

Terms are ANDed: `series:nains author:uderzo year:2010..2015 format:pdf ^genre:humour`; `partial:true`/`complete:true` say
whether an album's page was read; `--files` lists files instead of albums. The field list is in the queries guide.

## When unsure

Read the matching guide (importing, writing, maintaining, komga, queries, configuration) before running anything that
writes, and tell the user what the command will change before you run it.
