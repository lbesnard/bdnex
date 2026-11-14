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

Write the pattern in quotes in your shell if it has `^`, `$`, `*` or spaces. Add `-l` or `--json -l` to see
everything known about the results.

A word before a colon that is not a field name (as in `Tintin: Le Lotus`) is searched as plain text.

## Fields

- **Everywhere:** `any` (see above).
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
