---
name: using-bdnex
description: Operate bdnex, the library manager for French comics (Bédéthèque), safely on someone's collection. Use when the user asks to import, match, tag, rename, convert, query, repair or push to Komga their comics with bdnex, or asks how bdnex works.
---

bdnex keeps a SQLite library of comic files and the Bédéthèque albums they match. **The documentation is the source of
truth: <https://bdnex.readthedocs.io/en/latest/>.** Read the page for the job before running anything that writes:
*Safe workflows* (what touches files, previews, backups), *Getting started*, *Importing*, *Writing* (tags, move,
convert, repair), *Maintaining*, *Komga*, *Queries*, *Configuration*, and the cookbook (`How do I...?`, and
`Something does not work`). `bdnex <command> --help` is exact for flags. From a clone, run `uv run bdnex ...`.

## Rules (the long form is *Safe workflows*)

1. **Preview first.** `-n` (`--pretend`) changes nothing: show the user its output, then run for real.
2. **A first import touches no file**: `bdnex import --database-only --auto=all <folder>` (`--auto=all` asks nothing;
   drop it to check each match). Improve matches from the library, write tags or move files only afterwards.
3. **Only `write`, `move`, `repair`, `convert` change files** (and `import` with `-W`, `--move` or `--copy`): tags,
   renames, CBR/PDF to CBZ, rebuilt archives. Say so and offer a sample on one series first. A `copy` source is read-only.
4. **Back up the library file** (`library:` in the configuration) before bulk changes. Ways back: `bdnex undo` (a `move`),
   `bdnex trash --restore ID` (replaced files, including a damaged CBZ repaired in place).
5. **Secrets stay out of commands and output**: the fetcher address, Komga key, web password live in the configuration.
   `bdnex config` prints them: never paste its output.
6. **Check matches before spreading them**: `bdnex ls --long`, `bdnex ls --unmatched`, before `write`, `move` or `komga push`.
7. Never edit the user's real library or comics to try something: work on copies.

## First time

`bdnex config --edit` (set `collection_path` to the comics folder; `write.pdf` and `write.cbr` decide whether PDF and
CBR files are converted to CBZ, `import.sources` lists inbox folders), then `bdnex init` once (downloads the album
list), then a first import as in rule 2. `.cbr` files need `unrar`, `7z` or `bsdtar`.

## Common jobs

| Goal | Command |
|---|---|
| bring a folder in without touching files | `bdnex import --database-only --auto=all <folder>` (`-v` every file, `-vv` how, `--log-file import.log`) |
| fetch the details of link-only albums | `bdnex import --fetch --select partial:true` |
| what is in the library | `bdnex ls series:nains`, `bdnex ls complete:true -c`, `bdnex ls --unmatched` |
| missing summary or credits | `bdnex reparse -n` (offline, stored pages), `bdnex edit` for a hand fix |
| write tags into files | `bdnex write -n`, then `bdnex write` |
| organise into the folder tree | `bdnex move -n`, then `bdnex move` |
| damaged archives, duplicates, health | `bdnex repair -n`, `bdnex duplicates`, `bdnex check` |
| a Komga server (it ignores tags inside a PDF) | `bdnex komga show format:pdf`, `bdnex komga push -n`, then `push` (`--fields` sends only some) |
| browse in a browser | `bdnex serve` |

An import can be stopped with Ctrl-C and run again: decisions and the albums already complete are kept, and
`bdnex import --fetch` continues the page downloads. Pages are requested politely (`bedetheque.delay`,
`bedetheque.parallel`); a large collection takes hours.

## Queries (`ls`, `write`, `move`, `komga` ...)

Terms are ANDed: `series:nains author:uderzo year:2010..2015 format:pdf ^genre:humour`; `partial:true` / `complete:true`
say whether an album's page was read; `--files` lists files instead of albums. The field list is in *Queries*.

## When something is off

Look the message up in *Something does not work* (cookbook) before guessing; rerun the import with `-vv --log-file x.log`
and read the reasons it prints for each skipped file.
