# Safe workflows

How to use bdnex on a collection you care about: see before you change, and know what each command can touch. This is
the page to read before a big run, and the page a person (or an assistant such as Claude Code, see
[Getting started](getting-started.md#working-with-claude-code)) should follow when running bdnex for you.

## What can touch your files

| Command | Your comic files | The library (the database) |
|---|---|---|
| `ls`, `series`, `check`, `duplicates`, `komga show`, `serve` (read-only) | never | read |
| `import --database-only` | **never**: nothing is moved, copied, written, converted, replaced or deleted, whatever the configuration says | filled |
| `import` (no other flag) | not changed by default; `-W`, `--move` and `--copy` write, move or copy files | filled |
| `write`, `move`, `repair`, `convert` | **yes**: tags written, files renamed or moved, CBR and PDF converted to CBZ, damaged archives rebuilt | updated |
| `edit`, `reparse`, `relink`, `relocate`, `reset`, `trash` | no (`trash --empty` deletes files that were already replaced) | changed |
| `komga push` | never (it talks to Komga) | read |

A source whose `mode` is `copy` is read-only: nothing ever writes, moves, converts or deletes a file under it.

## Before anything big

1. **Preview.** `-n` (`--pretend`) shows what would happen and changes nothing: `bdnex move -n`, `bdnex repair -n`,
   `bdnex komga push -n`, `bdnex import -n`. Read it; then run without `-n`.
2. **Start without touching files.** `bdnex import --database-only --auto=all <folder>` fills the library and cannot
   change a file. Improve the matching from the library afterwards (`bdnex ls --unmatched`, `bdnex edit`), and
   only then write tags or move files.
3. **Back up the library.** It is one file (`bdnex config --defaults | grep library`). Copy it while no bdnex command
   runs, before anything that rewrites many records. `bdnex reset` makes its own copy first.
4. **Know the way back.** A `move` is taken back with `bdnex undo`. A file that was replaced (converted, repaired, a
   spare version) waits in the trash: `bdnex trash`, `bdnex trash --restore ID`. A first import never needs a way
   back, because it changes no file.
5. **Look at a sample.** Run the real command on one series (`bdnex write series:nains`) before the whole collection,
   and check the result with `bdnex ls --long series:nains`.

## Checks after a run

- `bdnex ls --unmatched -c` counts the files without an album; `bdnex ls partial:true -c` the albums whose page was
  not read; `bdnex ls complete:true -c` the complete ones.
- `bdnex check` compares the library with the disk and says what is wrong; `--fix` settles only the safe things.
- A wrong match is not a safe thing to publish elsewhere: look at it (`bdnex ls --long`) before `komga push`, `write` or
  `move` spread it into tags and file names.

## Secrets

The address of your page fetcher, the Komga API key and the web page's password live in your configuration
(`bdnex config --edit`), never on a command line, in a script you share, or in a commit. Note that `bdnex config`
prints the effective configuration **with** those values: do not paste its output in a bug report or a chat.

## When it does not work

[Something does not work](cookbook.md#something-does-not-work) lists the messages and what to do. `-v`, `-vv` and
`-vvv` say more about an import, and `--log-file PATH` keeps everything it did in a file
([Importing](importing.md)).
