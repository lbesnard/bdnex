# Checking the matches, with or without an assistant

A big import by `--auto=all` is mostly right and sometimes wrong. These commands find the wrong ones, show what
else a file could be, and let you (or an AI assistant such as Claude Code, working for you) fix them. **Nothing here
changes a comic file, and nothing is unlinked or rematched without a decision.**

| Step | Command | What it does |
|---|---|---|
| 1. Find | `bdnex check --matches` | weighs every match against its album's page; lists the ones that look wrong, changes nothing |
| 2. Flag | `bdnex check --matches --fix` | the same, and flags them (`review:true`) |
| 3. List | `bdnex review --list` (`--json`) | the flagged files and why |
| 4. Alternatives | `bdnex candidates --low` (`--json`, `--cover`) | for each file in doubt, the other albums it could be, with the evidence |
| 5. Decide | `bdnex review`, `bdnex import --album ADDRESS FILE`, `bdnex edit` | keep, unlink, choose again, or fix a field |

What the checks compare (the file's own tags, the volume, the year, the authors, the other files of the folder) is
described in [Importing](importing.md). The page of an album has to be read for the year and
the credits to be known: run `bdnex import --fetch` (and `bdnex import --reparse` for pages already stored) first, otherwise most
files are `unverified`.

## Settling the doubtful ones at once: `bdnex review --auto`

`bdnex review` asks about each flagged file. When there are thousands (an import flags every file whose cover does not
look like its album's, and a different edition or a PDF's first page often does not), `bdnex review --auto` weighs
**every flagged file and every match applied under `match.strong_threshold`** (an older `import --auto=all` applied the
ones in the middle unasked) and settles them by rule, asking nothing but one question before it changes anything.
It weighs each with what the library knows: the album's page, the file's own tags (year, author, address) and, with
OpenCV, the file's cover against the album's saved cover.

| Verdict | Means | What `review --auto` does |
|---|---|---|
| `kept` | something other than the name confirms it (the year, an author, the file's tags, a cover that looks alike by `match.cover_vote_min`) | the flag goes |
| `wrong` | something contradicts it (another volume, another series in its tags, a cover that looks little like the album's) | switches the file to a better album if the covers of its other candidates find one, else unlinks it |
| `unconfirmed` | nothing confirms it, nothing contradicts it | nothing: no evidence is no reason to ask you (a flag a medium match got is taken back) |

**The covers of the other candidates.** For each `wrong` file it compares the covers of its best other candidates
(`--candidates N`, 5 by default; `0` turns it off; `--no-cover` skips every cover) and says, for each file, the covers it
compared. A cover already saved is read from disk, any other is one request (a picture only; no page is read). When one
looks like the file's (`match.cover_vote_min`) and is clearly ahead of the album now linked and of the others (the rule
of the cover vote at the end of an import), the file is linked to it (the library only, as `import --album
--database-only` does). Otherwise it is unlinked and remembered as skipped, so `bdnex import --skipped` decides it again:
it comes back when it is strong, or when a cover clearly votes for one candidate.

A switch is counted only when the library shows it: if the better album already has another file (a duplicate), the import
refuses it, the file stays on its contradicted album, flagged with the reason, and the summary says so. The summary also says
why the unconfirmed ones could not be judged (most often: the album page was never read, so `bdnex import --fetch --select
partial:true` is the next step).

Files you chose (`import --album`, `review`) and the ones read from their own tags are never looked at. The usual order:

```bash
bdnex review --auto -n                # the numbers and what would change (the covers of the candidates are requested)
bdnex review --auto                   # settle them (asks once; -y to not ask)
bdnex import --skipped --auto=all     # then decide the unlinked ones again: strict, and the covers of the best candidates vote
bdnex review                          # what is still flagged, one by one
```

`-n` shows only the counts. `--json` prints the verdicts, with the evidence and the better album found, and changes
nothing. A digital edition whose cover differs from the print album's is contradicted by its cover alone: with no better
album it is unlinked, and `bdnex import --album` links it back. A shorter way to type it: an [alias](configuration.md#aliases-commands-with-a-name-that-says-what-they-do).

## A table, a batch at a time: `bdnex review --batch`

For checking by eye, a batch at a time, with every decision saved as you make it:

```bash
bdnex review --batch 50               # the next 50 doubtful files, in a table
bdnex review --batch 50 --unmatched   # the same, with the files that have no album
bdnex review --batch 50 series:batman # only some files (same terms as `bdnex ls --files`)
```

One row per file: its **name**, its **folder** (it often says the series or the language), the **album it is linked to now**,
the **match score** of the import, the **cover score** (the file's cover against the album's saved cover) and a **state**
(`contradicted`, `unconfirmed`, `no album`, then what you decided). The most doubtful come first: contradicted ones, lowest
cover first, then the unconfirmed, then (with `--unmatched`) the files without album. Under the table, the file's whole path
and what speaks for and against its match.

| Key | Does |
|---|---|
| `k` | keep the match (the flag goes, the file is marked `checked=keep`) |
| `u` | unlink the file (remembered as skipped, so `bdnex import --skipped --auto=all` decides it again) |
| `o` | **another album**: search the album list (the sitemaps) by words, starting from the file's name; type to filter, Enter picks, Ctrl-O opens the page |
| `i` | an album's page address or number |
| `s` | skip for now (nothing is saved: it is in the next batch again) |
| `l` | leave it as it is, and do not show it again |
| `p` | open the page of the album now linked, in your browser |
| `n` | the next batch |
| `q` | quit |

Each decision is **written at once** to the library (a `checked` mark, the flag, the link), so quitting, Ctrl-C or a lost SSH
connection loses nothing. Files already checked are not shown again: **run the same command later to continue**. At the end
of a batch it asks whether to check the next one. It needs the optional `tui` extra (Textual): `pip install 'bdnex[tui]'`
or `uv sync --extra tui`; without it, `bdnex review` and `bdnex review --auto` work as before. Linking to another album
reads that album's page (a request), so it takes a moment; a file whose chosen album already has another file is refused
and says so.

## By hand

```bash
bdnex check --matches               # how many look wrong, and why (changes nothing)
bdnex check --matches --fix         # flag them for `bdnex review`
bdnex candidates --low --limit 5    # the other albums they could be
bdnex review                        # one by one, with the two covers side by side
```

## With an assistant (Claude Code)

The repository ships a skill, `skills/using-bdnex` (see [Getting started](getting-started.md)), that teaches the
assistant the safety rules and these commands. Copy it to `~/.claude/skills/`, open Claude Code in your project and ask,
for instance:

> Check the matches of my library. Run `bdnex check --matches`, then `bdnex candidates --low --json`, and for each
> file say which album it is, how sure you are and why. Don't change anything until I say so.

What the assistant does with it:

1. reads the evidence (`--json` gives the file, the album now linked, every candidate with its score, the signs for
   and against, and with `--cover` the cover similarity);
2. proposes, per file, **keep**, **another album** (its address) or **leave**, with a confidence (high when several
   independent signs agree, such as the year, an author and the cover; low when only the name matches);
3. after you approve, runs the command that does it: `bdnex import --album ADDRESS FILE` (the album it found, nothing asked),
   `bdnex review` (keep or unlink) or `bdnex edit` (a wrong field). It never edits the database file.

`--cover` compares the file's cover with each candidate's: it reads the cover the import already saved (`cover.download`) and makes one request for any other candidate (so keep `--limit` small),
needs OpenCV, and, for an album whose page was not read, looks at the address the site uses for covers, which can be
wrong (that candidate is then not compared). A cover that is *very* like the file's settles most questions of a reissue
against an original; a low similarity alone proves nothing (scans are cropped and compressed).

To remember what was looked at, so that a long check can stop and resume, the assistant marks each file it dealt with
(`bdnex mark FILE-QUERY --set checked=keep`, or `checked=other:245127`), and asks only for the others:
`bdnex candidates --low ^attr:checked --json`. You can list what it decided with `bdnex ls --files attr:checked=keep`.
The mark says what was decided, not that it was applied: only `bdnex import --album` changes a match.

An assistant is a reader of the evidence, not an authority: it can be wrong, it cannot see your files, and it does not know
which edition you want. Keep it to **proposals**, check the ones it is not sure about with `bdnex review`, and
back up the library first ([Safe workflows](safe-workflows.md)). Nothing leaves your machine except what the assistant
itself sends to its provider (file names and album titles are part of that: do not use it if that is a concern).
