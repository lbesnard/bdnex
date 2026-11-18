# Changelog

bdnex is pre-1.0: commands, options and the library schema change, sometimes in ways that break. **Breaking** changes
are marked. Back up your library before upgrading (copy the library file while no bdnex
command runs, never while an import is fetching).

## Unreleased

### Added
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

### Changed
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
