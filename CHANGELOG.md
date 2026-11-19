# Changelog

bdnex is pre-1.0: commands, options and the library schema change, sometimes in ways that break. **Breaking** changes
are marked. Back up your library before upgrading (copy the library file while no bdnex
command runs, never while an import is fetching).

## Unreleased

### Changed
- **OpenCV is a normal dependency** (the cover check, `recheck`, the cover vote need it): `uv sync` and `pip install bdnex`
  install it. An optional extra was dropped by every plain `uv sync`, and the covers were then silently not compared.
  `bdnex[cover]` still installs (an empty extra). The Docker image grows by about 100 MB.

### Added
- **`bdnex recheck`**: the matches applied under the strong threshold (the medium ones an older `--auto=all` applied
  unasked), weighed again with the album's page, the file's tags and the saved covers. Confirmed ones and the ones
  nothing confirms are left alone; the contradicted ones are flagged for `bdnex review`, or unlinked with `--unlink` so
  that `import --skipped --auto=all` (strict, then the cover vote) decides them again. For each of them the covers of the
  5 best other candidates are compared (`--candidates`); `--rematch` links the file to one that is clearly its cover.
- **`bdnex review --auto`** settles every flagged file by the same rule, asking nothing: confirmed, the flag goes;
  contradicted, switched to the album whose cover is the file's, else unlinked; the others stay flagged.
- **`import --auto=all` is strict by default** (**breaking**): it applies only strong matches without a doubt. A match in
  the middle used to be applied unasked (`match.auto_all: medium` brings that back); now the file stays skipped.
- **The cover vote** (`match.cover_vote`, `match.cover_vote_min`): at the end of the run, at low priority, the covers of
  the best candidates of the files left undecided are compared with the file's cover; one that looks like it (60%) and
  clearly more than the others decides the file (`cover vote 78%`). Needs OpenCV.
- **API:** `GET /api/candidates` (the albums each file could be, with the evidence and, with `cover=true`, the cover
  comparison), `GET /api/check?matches=true`, `POST /api/marks` and `marks` on an album and its files, `GET /api/fields`,
  `GET /api/history` (who changed what, from the page and the API), `GET /api/jobs/{id}/events` (a task's output as
  server-sent events), the review answers `rematch` and `album`, and the tasks `update`, `komga-show` and `komga-push`.
  `/api/albums` and `/api/files` are cut in the database and say how many match in all.
  **Breaking:** `GET /api/stats` is now what `bdnex stats --json` prints (it takes `q`).
- Match question: **I** enters an album Id (the number at the end of its page address) or an address, then shows the
  album for you to check (like beets' *enter Id*). It replaces the old **U** *Type the album's URL...* (**breaking** for
  that key only).
- `bdnex import --album ADDRESS|NUMBER FILE`: use that album for the file, without searching or asking.
- `bdnex stats [QUERY]` (albums read or only matched, files, formats, sizes, stored pages) and `bdnex fields` (the
  fields a query can use).
- Queries: a lone `,` is **or**, `field+` / `field-` sorts, and `bdnex ls --limit N`.
- `bdnex config` hides the secrets (`<hidden>`); `bdnex config --clear` shows them.
- `bdnex check --matches [QUERY]` weighs every match against its album page (the file's own tags, volume, year,
  authors, its folder) and lists the ones that look wrong; `--fix` flags them for `bdnex review`.
- `bdnex candidates [--low] [--cover] [QUERY]`: the other albums a file in doubt could be, with the evidence.
- Matching is **precision first**: a strong first choice is asked about when another album is within
  `match.min_margin` points, or when its page contradicts the file. On a 16,000-file library the strong matches that
  were wrong went from 0.4% to 0.08%. `tools/eval_matching.py` measures this on your own library.
- The parser reads the inking (`Encrage` is an Inker), the other credits Bédéthèque lists (preface, storyboard,
  background, design, adapted from), the icons of "Autres infos" and writes `Collection`/`Cycle` as ComicInfo
  `Imprint`/`StoryArc`. Run `bdnex reparse`, then `bdnex write`.
- `bdnex candidates` shows where each score comes from (the title score, then the volume, year and author), and the four
  adjustments are the options `match.volume_bonus`, `volume_penalty`, `year_bonus`, `author_bonus`.
- `bdnex mark` puts your own marks on files or albums (`--set checked=keep`, `--remove`), and any query finds them with
  `attr:checked` or `attr:checked=keep` (and `^attr:checked` for the others). **Library schema v10**: the first
  command after upgrading copies your library to `<library>.bak-v9` and then migrates it; an older bdnex can no longer
  open it.
- `bdnex update` reads the files again after another tool changed them (size, checksum, pages, damage, their own tags);
  the opposite of `write`. `bdnex check` also lists comic files of the collection folder that the library does not know.
- Path templates have functions: `%upper{}`, `%lower{}`, `%title{}`, `%capitalize{}`, `%left{}`, `%right{}`, `%asciify{}`,
  `%first{}`, `%if{}`, `%ifdef{}`; a typo in one is refused when the configuration is read.
- `hooks:` in the configuration (`after_import`, `after_write`, `after_move`): commands of your own that run afterwards.
- Docs: a *Command reference* generated from the commands' own `--help` (a test keeps it current), and *Checking the
  matches* (by hand or with an assistant).

### Fixed
- Albums stored with the old role `Encrage` (not yet re-read) lost their inkers in the Komga push and the written
  ComicInfo: the old name is read as `Inker`.
- CBR files that a `7z` without the RAR codec could not read (`Failed the read enough data` in the log, no cover, no
  write): `unrar`, `unar` and `bsdtar` are tried before `7z`.
- Ctrl-C during an import stops at once: the requests waiting for their polite delay give up (they kept the
  program alive for up to ten seconds each, so it took several Ctrl-C), and `import --database-only` no longer goes
  through the file-placement step, which looked up every imported file again for nothing.

### Changed
- `import -v`: the match score is a percentage in its own colour (red to green), and each cover comparison prints
  `cover: 87%  file name`.
- `bdnex komga push` leaves out the files whose match waits for a review (`--with-review` sends them): a doubtful
  match no longer puts another album's metadata, locked, in Komga.
- The web page's review answers and `bdnex review` share one function; a test checks every flag a web task builds
  against its command's `--help`.
- Covers are downloaded (and compared with the file's cover) in the background as each album completes, no longer
  all at the end of the run: an interrupted or still-running import already has the covers and scores of what it did.
  `bdnex candidates --cover` reads the saved covers instead of requesting them again.
- **Breaking:** `bdnex review --scan` is now `bdnex check --matches --fix` (the old spelling still works for a release);
  `bdnex review -l` is `--list`.
- **Breaking:** the import options `--no-fetch`, `--retry-partial` and `--recache` are gone (`--match`, `--fetch`,
  `--fetch --force`); the config option `match.use_facts` is gone (`bdnex config --update`).
- `bdnex --help` groups the commands by task.

## 0.10.0

- `bdnex komga show|push`: send the library's metadata (credits, summary, genres, series status and total...) to a Komga
  server through its API (Komga ignores the tags inside a PDF).
- The import fetches album pages while it matches, and builds each album as its pages arrive (`bdnex reparse` builds
  them offline from stored pages).
- A file's own `Web` address (CBZ, CBR or an attachment of a PDF) names its album when that album is in the album list.
- In-place repair keeps the damaged original in the trash; `bdnex completion` and the docs follow the commands.
