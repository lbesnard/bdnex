# Querying the library

`bdnex ls` lists albums of the library, or files with `--files`. Each argument is
one term, and **all terms must match**. Searches ignore case and accents
(`asterix` finds *Astérix*).

```bash
bdnex ls                              # everything
bdnex ls astérix                      # free text: title, series, authors, file name
bdnex ls series:nains
bdnex ls author:uderzo                # any credit
bdnex ls penciller:chemineau          # a given role
bdnex ls genre:fantasy year:2010..2015
bdnex ls kind:oneshot
bdnex ls any:soleil                   # anywhere: title, series, publisher, credits, summary...
bdnex ls '/^13m[0-9]+$/'              # a regular expression, on any text field
bdnex ls 'title:/^les .*nains$/'
bdnex ls '^genre:humour'              # a leading ^ negates a term
bdnex ls complete:false               # matched, but the album page was not read yet
bdnex ls --files corrupted:true       # damaged files
bdnex ls --files tagged:false         # registered files not yet matched to an album (`bdnex ls --unmatched` also lists skipped and failed ones)
```

## Searching the whole album list: `--sitemap`

`bdnex ls` looks in your library. With **`--sitemap`** the words are searched in the whole album list instead (the sitemap: every album of the
site, whether you own it or not), the way `fzf` searches it in the review screen: every word has to be in the album's name (case and accents do not
matter); when few names have every word, the letters you typed are looked for in order, spaces ignored, and a number at the end is the volume
(`batmandarkvic2` finds `Batman Dark Victory Tome 2`); a typo is forgiven; and nothing is requested (the list is stored in the library). `--limit` defaults to 20; `--json` and `-c` work as usual.

```
bdnex ls --sitemap batman dark victory           # the albums of the whole list that look like these words
bdnex ls --sitemap --for 'Yoko Tsuno - Integrale T02.cbr' --json
```

**`--for PATH`** (repeatable, and it implies `--sitemap`) searches for the albums a file could be: by its name, by the names of its last two
folders (often the series) and by the volume in its name, and again without the words an edition adds (`intégrale`, `tome`, `deluxe`), the albums whose name has the file's words and the right volume first, each with a line
saying what moved its score and which searches found it. To link a file to an album you found, give the address to
`bdnex import --album ADDRESS FILE`.

## Term syntax

| Form | Meaning |
|---|---|
| `text` | free text on title, series, authors and file name |
| `field:value` | case/accent-insensitive substring |
| `field:=value` | exact match |
| `field::regex` or `field:/regex/` | regular expression, ignoring case and accents (`title:/^les .*nains$/`) |
| `any:text`, `any::regex` | the text in **any** text field: title, series, publisher, ISBN, summary, URL, credits, genres, file name and path |
| `::regex` or `/regex/` | a regular expression on any text field |
| `field:2010..2015` | numeric range; `2010..` and `..2015` are open ended |
| `field:true` / `false` | yes/no fields |
| `^term` | negation |
| `a b , c` | a lone comma is **or**: albums matching `a` and `b`, or `c` (each side is ANDed). The comma must be a word of its own |
| `attr:name`, `attr:name=value` | a mark you put with [`bdnex mark`](maintaining.md#your-own-marks-bdnex-mark): the file (or album) has it, or has it with that value |
| `year-`, `title+` | sorts by that field, newest/last first (`-`) or oldest/first (`+`); several give several keys. See `bdnex fields` |

`bdnex fields` lists every field with its kind (text, number, yes/no). `bdnex ls year- --limit 10` shows the ten most
recent albums. Or and sort work in every command that takes a query (`review`, `write`, `komga`, `candidates`...);
`--limit N` is an option of `bdnex ls`.

`bdnex stats [QUERY]` counts what a query matches (the whole library without one): albums whose page was read and
those only matched, files by format and size, damaged and flagged files, and the pages stored. `--json` for a script.

Write the pattern in quotes in your shell if it has `^`, `$`, `*` or spaces. Add `-l` or `--json -l` to see
everything known about the results.

A word before a colon that is not a field name (as in `Tintin: Le Lotus`) is searched as plain text.

## Fields

- **Everywhere:** `any` (see above), and `attr` (your own marks, `attr:name` or `attr:name=value`: see `bdnex mark`).
- **Text:** `title`, `series`, `publisher`, `isbn`, `language`, `summary`, `kind` (`series` or `oneshot`),
  `url`, `source`, `genre`, `author` (any credit), `writer`, `penciller`, `inker`, `colorist`, `letterer`, `coverartist`.
- **Numbers:** `id` (the album's number, as the web page and `bdnex edit` know it), `year`, `volume`, `pages`, `rating`, `files` (files per album; `files:0` finds albums without a file).
- **Yes/no:** `complete` (the album page was read; `partial` is its opposite).
- **File fields:** `path`, `filename`, `format` (`cbz`, `cbr`, `pdf`), `md5`, `embedded` (what the file's own `ComicInfo.xml` says), `size`, `fileid` (the file's number), `filepages` (pages of the file),
  `corrupted`, `review` (the match waits for `bdnex review`), `metadata` (file already has a `ComicInfo.xml`), `tagged` (only with `--files`),
  `spare` (an extra version of its album), `staged` (imported but not moved into the library yet) and
  `cover` (similarity of its cover with the album's, 0-100; `cover:..40` finds the doubtful ones).
  Without `--files`, a file field selects the albums that have a matching file.

## Output

| Option | Effect |
|---|---|
| `--files` | one line per file instead of per album |
| `--format TEMPLATE` | custom line, e.g. `'$series T$vol - $title ($year)'` |
| `-c`, `--count` | only the number of results |
| `-l`, `--long` | everything known about each result: credits by role, publisher, date, pages, genres, rating, ISBN, summary, source and URL, and the linked files. With `--files`: each file and its album |
| `--json` | machine-readable output (with `--long`: the complete records, credits and files included) |

Template variables: `$label` (`Series - T01 - Title`, or just the title for a one-shot), `$series`,
`$vol`, `$volume`, `$title`, `$authors`, `$genres`, `$publisher`, `$year`, `$isbn`, `$pages`, `$rating`,
`$kind`, `$files`, and with `--files` also `$path`, `$format`, `$size`, `$md5`. An unknown variable is
left as written.

Exit status: 0 on success, 1 for a configuration or missing-library problem, 2 for an invalid query.
