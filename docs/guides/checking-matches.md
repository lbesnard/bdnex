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
the credits to be known: run `bdnex import --fetch` (and `bdnex reparse` for pages already stored) first, otherwise most
files are `unverified`.

## The matches applied under the strong threshold: `bdnex recheck`

Before `import --auto=all` became strict, a match in the middle (a score between `medium_threshold` and
`strong_threshold`) was applied unasked, so a library built that way holds some. `bdnex recheck` weighs each of them
again with what the library already knows: the album's page, the file's own tags (year, author, address) and, with
OpenCV, the file's cover against the album's *saved* cover (computed here when it was not, and kept for the next time).

| Verdict | Means | What `recheck` does |
|---|---|---|
| `kept` | something other than the name confirms it (the year, an author, the file's tags, a cover that looks alike by `match.cover_vote_min`) | nothing (it takes back a flag an earlier `recheck` put) |
| `wrong` | something contradicts it (another volume, another series in its tags, a cover that looks little like the album's) | flags it for `bdnex review`, or unlinks it with `--unlink` |
| `unconfirmed` | nothing confirms it, nothing contradicts it | **nothing**: no evidence is no reason to ask you (`--flag-unconfirmed` flags them, `--unlink --unconfirmed` unlinks them) |

**The covers of the other candidates.** For each `wrong` file, `recheck` also compares the covers of its best other
candidates (`--candidates N`, 5 by default; `0` turns it off; `--no-cover` skips every cover). A cover already saved is
read from disk, any other is one request (a picture only; no page is read). When one looks like the file's
(`match.cover_vote_min`) and is clearly ahead of the album now linked and of the others (the rule of the cover vote
at the end of an import), it is shown as `better:` and, with `--rematch`, the file is linked to it (the library only,
as `import --album` does with `--database-only`). Without `--rematch` it is only written in the flag's reason.

Files linked at a confident score, the ones you chose (`import --album`, `review`) and the ones read from their own
tags are never looked at. The usual order:

```bash
bdnex recheck -n                      # the numbers and the contradicted ones, and the better albums found; changes nothing
bdnex recheck --rematch --unlink      # switch to the better album when a cover says so, else unlink (asks first; -y)
bdnex import --skipped --auto=all     # then decide the unlinked ones again: strict, and the covers of the best candidates vote
```

Without `--unlink` the contradicted files are only flagged. An unlinked file is remembered as skipped, so `import
--skipped` picks it up again; it comes back only when it is strong, or when a cover clearly votes for one candidate.

### Files already flagged: `bdnex review --auto`

`bdnex review` asks about each flagged file. When there are thousands (an import flags every file whose cover does not
look like its album's, and a different edition or a PDF's first page often does not), `bdnex review --auto` gives
the same verdicts as `recheck` to *every* flagged file and applies them by rule, asking nothing: confirmed by
something other than the name, the flag goes; contradicted, the file is switched to the album whose cover is
clearly the file's (`--candidates`), else unlinked; the others stay flagged for you. `-n` shows the counts, `-y`
skips the one confirmation. A digital edition whose cover differs from the print album's is contradicted by its
cover alone: with no better album it is unlinked, and `bdnex import --album` links it back.

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
