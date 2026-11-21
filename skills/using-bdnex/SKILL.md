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
| what is in the library | `bdnex ls series:nains`, `bdnex ls complete:true -c`, `bdnex ls --unmatched`, `bdnex stats`, `bdnex fields` |
| missing summary or credits | `bdnex import --reparse -n` (offline, stored pages), `bdnex edit` for a hand fix |
| write tags into files | `bdnex write -n`, then `bdnex write` |
| organise into the folder tree | `bdnex move -n`, then `bdnex move` |
| damaged archives, duplicates, health | `bdnex repair -n`, `bdnex duplicates`, `bdnex check` |
| a Komga server (it ignores tags inside a PDF) | `bdnex komga show format:pdf`, `bdnex komga push -n`, then `push` (`--fields` sends only some) |
| browse in a browser | `bdnex serve` |

An import can be stopped with Ctrl-C and run again: decisions and the albums already complete are kept, and
`bdnex import --fetch` continues the page downloads. Pages are requested politely (`bedetheque.delay`,
`bedetheque.parallel`); a large collection takes hours.

## Keeping the whole catalogue

`bdnex mirror -n` says how many albums of the album list the library lacks and how much room they take; `bdnex mirror --limit N` fetches some (page, series
page, cover), `bdnex mirror` all that is left (it uses the user's own fetcher and endpoints: preview first, start with a limit, never run it for the user without
asking). It stops by itself after 10 failures in a row (the site is probably blocking); running it again continues.

## A first pass over the doubtful matches (so the person's review table shows only the rest)

The person checks the matches in `bdnex review --batch 50` (a terminal screen: never run it for them). You can read the same evidence without the screen and
confirm the obvious ones, so that their table, which skips every file marked `checked`, only shows what you were not sure about. No new tool is needed:

1. **Read**: `bdnex review --auto -n --json` (read-only). Each file has a `kind`: `kept` (something other than the name confirms the match), `wrong`
   (something contradicts it), `unconfirmed` (nothing confirms it), with the evidence, the cover score and, for the wrong ones, a `better` album.
2. **Propose, never apply on your own**: show the person the numbers and ten examples of what you would confirm, and wait for a yes. Confirm only
   `kept` files, and `unconfirmed` ones whose cover score is at least `cover.match_percentage` and that have nothing against them. Leave
   every `wrong`, every file with a `better` album and every low cover to the person.
3. **Apply with a mark**: `bdnex mark -n --set checked=keep <query>` first, then without `-n`, one `fileid:N` (or a narrow query) at a time. A mark changes
   nothing else: the match, the files and the flags stay as they are.
4. **Never** run `bdnex review --auto` without `-n` (it applies a rule: switches and unlinks), never mark `checked=unlink`, never run `bdnex import`
   or `bdnex mirror` unasked.

**What a cover score means.** It is not a "percent alike": it is the share of distinctive features of one cover found in the other. Measured on a real library
(`tools/eval_covers.py`: each file against its own album's cover, against an album of another series and against the next volume of its own series): a right match
scores about 50 (median), not near 100; the AUC is 0.98 against another series and 0.95 against the neighbouring volume, so the cover is a reliable check. The default
`cover.match_percentage` is 3: it flags about 5 % of the right matches (an edition with another cover, a first page that is no cover), catches all the wrong-series
matches and about 43 % of the wrong-volume ones. A cover that scores high does not prove the volume: volumes of one series can share a layout.

## Checking the matches

`bdnex review --auto -n --json` weighs every flagged file and every match applied under the strong threshold (kept / wrong / unconfirmed) and, for the wrong ones, the covers of the 5 best other candidates (`better`); without `-n --json` it settles them by rule: the flag goes for the kept, the wrong are switched to the better album or unlinked (ask first), the rest stay. `bdnex review --batch 50` is the screen a person uses for thousands of doubtful matches (never run it for them: it needs a terminal). The user can shorten commands with `aliases` in the configuration. `bdnex check --matches` weighs every match against its album page (credits, year, volume, the file's own tags, its folder) and
lists the ones that look wrong (it has no `-n` and no `--json`: it only reads, unless you add `--fix`, which flags them for `bdnex review`). `bdnex review --list --json` gives the flagged files with
the reason, for you to read. `bdnex candidates --low --json` gives, for each file in doubt, the other albums it could be
(score, evidence for and against, `page not read` when only the sitemap is known); add `--cover --limit 3` to compare covers
(one request per candidate; ask first). The procedure is the docs page *Checking the matches*: find, list the alternatives, then
report per file keep / another album / leave with a confidence and why. To check one: `bdnex ls --files <name> -l` for the album, the cover (`bdnex review` shows
both covers) and the evidence. To fix it, never edit the database: `bdnex import --album <address|number> <file>` (applies the album you name; ask the
user first), `bdnex import --match --force <file>` (the user chooses again), `bdnex edit` for a wrong field, or `bdnex review` (keep, unlink, rematch). Propose each change to the
user with your evidence and your confidence; do not change matches on your own. Record what you checked with
`bdnex mark <query> --set checked=<keep|other:ID|leave>` and ask for the rest with `bdnex candidates --low ^attr:checked --json`.

## Queries (`ls`, `write`, `move`, `komga` ...)

Terms are ANDed: `series:nains author:uderzo year:2010..2015 format:pdf ^genre:humour`; `partial:true` / `complete:true`
say whether an album's page was read; `--files` lists files instead of albums. The field list is in *Queries*.

## When something is off

Look the message up in *Something does not work* (cookbook) before guessing; rerun the import with `-vv --log-file x.log`
and read the reasons it prints for each skipped file.
