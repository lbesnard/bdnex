# Command reference

Every command with its options, as `bdnex <command> --help` prints them (this page is generated from the program:
see the guides for how to use them). `bdnex --help` lists the commands by task.

## Import and match

### `bdnex init`

Download or refresh the album list that file names are matched against.

```text
usage: bdnex init [-h] [-v] [--log-file PATH]

``bdnex init``: download (or refresh) the album list the matching works from.

options:
  -h, --help       show this help message and exit
  -v, --verbose    more detail: -v what is decided and why (every file), -vv how
                   (reads, cache, requests, threads), -vvv everything, libraries and
                   SQL included
  --log-file PATH  also write everything, at the debug level and with the time, to
                   this file (for a long run)

The matching of file names works offline from this list (about 55 files, 70 MB). Run
it once before your first import, and again whenever you want to pick up new albums.

Examples:
  bdnex init  download the album list that file names are matched against (once; run it again to refresh)
```

### `bdnex import`

Match comic files to Bédéthèque albums and record them in the library.

```text
usage: bdnex import [-h] [-m] [-f] [--skipped] [--sources] [-s TERM] [--as-is]
                    [--unattended] [--force] [--auto [=all]] [--no-auto] [--reparse]
                    [-n] [-W] [--move | --copy | --no-move] [--album ADDRESS|NUMBER]
                    [--database-only] [--cover-check | --no-cover-check]
                    [--series | --no-series] [--workers N] [-v] [--log-file PATH]
                    [paths ...]

``bdnex import``: match files to Bédéthèque albums and record them in the library.

positional arguments:
  paths                 comic files or folders (default: your library folder,
                        `collection_path` in the configuration; with --fetch alone:
                        everything in the library)

options:
  -h, --help            show this help message and exit
  --skipped, --skipped-earlier
                        only the files you (or the matching) skipped at an earlier
                        import, asked about again; `bdnex ls --unmatched` lists them
  --sources             the folders of `import.sources` in the configuration: what is
                        new in them is copied or moved into the collection, each
                        source in its own mode. Added to the paths, if any
  -s TERM, --select TERM
                        work on the files already in the library that match this
                        `bdnex ls --files` term (repeatable, all must match):
                        `--select tagged:false`, `--select 'author:uderzo'`. Added to
                        the paths, if any
  --as-is               only record the files: name, path, format, size, pages,
                        checksum and what their own ComicInfo.xml says. No matching,
                        no network, nothing moved or written. `bdnex import`
                        (matching) can be run on them later
  --unattended          ask nothing: apply the confident matches (`--auto`), leave the
                        others skipped for you (`--skipped` asks about them later), no
                        question about duplicates, name clashes or conflicts (the
                        database wins, a clash gets a number, originals are kept).
                        What `bdnex serve` uses to watch folders
  --force               redo the selected steps for files that are already done
                        (rematch, read the pages again)
  --auto [=all]         do not ask about confident matches (score >=
                        match.strong_threshold); --auto=all never asks: only strong
                        matches are applied (match.auto_all: medium applies the medium
                        ones too), the rest is skipped, and the covers of its best
                        candidates decide some of it at the end (match.cover_vote)
  --no-auto             check every file, whatever import.auto says
  --reparse             rebuild albums from the pages already stored in the library,
                        offline (no request, no file read): after bdnex learned to
                        read more of a page. `--select TERM` limits it to some albums
                        (same terms as `bdnex ls`); values already in the library are
                        never erased
  -n, --pretend         show what would be done; change nothing
  -W, --write           afterwards, write ComicInfo.xml into the imported files (in
                        place)
  --move                afterwards, write tags and move the files into the library
                        tree
  --copy                afterwards, copy the files into the library tree (originals
                        untouched)
  --no-move             leave files from outside your library folder where they are
                        (`import.outside`); `bdnex move staged:true` places them later
  --album ADDRESS|NUMBER
                        use this Bédéthèque album for the one file given (its page
                        address, or its number), instead of searching: nothing is
                        asked, and a file already imported is redone. `bdnex
                        candidates` finds the numbers
  --database-only       only fill the library: no comic file is moved, copied,
                        written, converted, replaced or deleted, whatever the
                        configuration says (a duplicate is skipped, not replaced)
  --cover-check, --no-cover-check
                        compare each file's cover with its album's (one request per
                        album; needs `pip install opencv-python-headless`); default:
                        `cover.check`
  --series              assume the files of a folder are the volumes of one series,
                        without asking (default: asked when the names do not make it
                        obvious; `import.series`)
  --no-series           match every file by itself, never as a folder
  --workers N           processes matching file names in the background, and reading
                        the files for --as-is at once (0: the cores less one; 1: one
                        by one); default: `import.workers`
  -v, --verbose         more detail: -v what is decided and why (every file), -vv how
                        (reads, cache, requests, threads), -vvv everything, libraries
                        and SQL included
  --log-file PATH       also write everything, at the debug level and with the time,
                        to this file (for a long run)

steps (default: both):
  -m, --match           find the album of each file, from the album list (offline; run
                        `bdnex init` first)
  -f, --fetch           read the album pages: authors, publisher, summary, genres...
                        (slow, politely)

With neither --match nor --fetch, both steps run. A step that is already done for a
file is skipped (the report says so); --force redoes it.

Examples:
  bdnex import --database-only --auto=all ~/comics     bring a folder in, touching no file and asking nothing
  bdnex import -n ~/comics                             preview: what would be matched, nothing is recorded
  bdnex import --skipped --auto=all                    ask again about the files skipped earlier (run `bdnex init` first)
  bdnex import --fetch --select partial:true           read the pages of the albums that are only matched so far
  bdnex import --album 123456 ~/comics/Nains-T01.cbz   use this album (the number at the end of its page address) for one file
  bdnex import --match --force ~/comics/Nains-T01.cbz  choose the album of one file again
  bdnex import --reparse --select series:nains         rebuild some albums from the pages already stored (offline)
```

### `bdnex candidates`

For files whose match is in doubt: the other albums they could be, with the evidence.

```text
usage: bdnex candidates [-h] [--low] [--all] [--limit N] [--cover] [--json] [-v]
                        [--log-file PATH]
                        [query ...]

``bdnex candidates``: for files whose match is in doubt, the other albums they could
be, with the evidence.

positional arguments:
  query            files to look at, same terms as `bdnex ls --files`

options:
  -h, --help       show this help message and exit
  --low            only the files whose match is in doubt: no album, flagged, looking
                   wrong (see `bdnex check --matches`), or matched with a score under
                   `match.strong_threshold`
  --all            look at every file of the library: needed when you give neither a
                   query nor --low (with --cover that can be tens of thousands of
                   cover requests)
  --limit N        candidates per file (default 5)
  --cover          also compare the file's cover with the cover of each candidate (a
                   cover the import already saved is read from disk; any other is one
                   request, so keep `--limit` small; needs OpenCV). The candidate
                   whose cover is most like the file's is marked
  --json           JSON, for a script or an assistant
  -v, --verbose    more detail: -v what is decided and why (every file), -vv how
                   (reads, cache, requests, threads), -vvv everything, libraries and
                   SQL included
  --log-file PATH  also write everything, at the debug level and with the time, to
                   this file (for a long run)

Nothing is changed. The candidates come from the album list stored in the library, so
without --cover nothing is requested. To redo a file: `bdnex import --match --force
FILE`, or `bdnex review --auto` to settle the doubtful ones by rule.

Examples:
  bdnex candidates --low                                the files whose match is in doubt, with the other albums each could be
  bdnex candidates --all --limit 3                      every file of the library (a query or --low is needed otherwise)
  bdnex candidates series:nains --limit 3               the 3 best albums for each file of one series (any file, in doubt or not)
  bdnex candidates --low --cover --limit 3 series:saga  also compare covers (one request per cover not saved yet)
  bdnex candidates --low --json                         the same as JSON, for a script or an assistant
```

### `bdnex review`

Go through the doubtful matches: one by one with both covers, or settled by rule with --auto.

```text
usage: bdnex review [-h] [--list] [--json] [--auto] [--batch N] [--unmatched]
                    [--candidates N] [--no-cover] [-y] [-n] [-v] [--log-file PATH]
                    [query ...]

``bdnex review``: go through the flagged matches one by one, with the file's cover and
the album's side by side.

positional arguments:
  query            only these files, same terms as `bdnex ls --files` (e.g.
                   series:nains)

options:
  -h, --help       show this help message and exit
  --list           only list the flagged files and why; ask nothing
  --json           with --list: the flagged files as JSON, with the evidence; with
                   --auto: the verdicts as JSON (nothing is changed), for a script or
                   an agent
  --auto           settle by rule, asking nothing, every flagged file and every match
                   applied under `match.strong_threshold`: the ones confirmed by
                   something other than the name lose their flag; the ones something
                   contradicts are switched to another album whose cover is clearly
                   the file's (see --candidates), else unlinked (`bdnex import
                   --skipped --auto=all` decides them again); the ones nothing
                   confirms or contradicts are left as they are
  --batch N        a table of the next N files to check (for example `--batch 50`):
                   file, folder, album now, match score, cover score; keep, unlink or
                   pick another album from the album list. Every decision is saved at
                   once and the files checked are not shown again: run it again to
                   continue. Needs the `tui` extra (`pip install 'bdnex[tui]'`)
  --unmatched      with --batch: also the files that have no album
  --candidates N   for each contradicted file, compare the covers of its N best other
                   candidates (the saved cover when there is one, else one request
                   each; needs OpenCV) and propose the album whose cover is clearly
                   the file's; 0: do not (default 5)
  --no-cover       do not compare covers at all (neither the album's, not yet
                   compared, nor the candidates')
  -y, --yes        with --auto: do not ask for confirmation before unlinking or
                   switching
  -n, --pretend    show what would be settled (with --auto); change nothing
  -v, --verbose    more detail: -v what is decided and why (every file), -vv how
                   (reads, cache, requests, threads), -vvv everything, libraries and
                   SQL included
  --log-file PATH  also write everything, at the debug level and with the time, to
                   this file (for a long run)

A match is flagged when its cover is not like its album's cover (similarity below
`cover.match_percentage`) or the album's page contradicts the file (year, volume, the
file's own tags, its folder: see `bdnex check --matches`), and nobody was there to
look at it. Each flagged file is shown with both covers side by side (the file's on
the left, the album's on the right) and you keep the match, unlink the file, ask for
another album, or leave it for later. `bdnex candidates` lists the other albums a file
could be.

Examples:
  bdnex review                         go through the flagged matches one by one, both covers side by side
  bdnex review series:nains            only the files of one series
  bdnex review --batch 50              a table of the next 50 files to check; keep, unlink or pick another album; continue later
  bdnex review --batch 50 --unmatched  the same, with the files that have no album too
  bdnex review --list                  only list the flagged files and why; ask nothing
  bdnex review --auto -n               preview the rule: how many are confirmed, switched, unlinked; changes nothing
  bdnex review --auto                  settle the doubtful matches by rule (asks once before changing anything)
```

## Look at the library

### `bdnex ls`

List and search the library.

```text
usage: bdnex ls [-h] [--files] [--format FORMAT] [--limit N] [-c] [-l] [--json]
                [--unmatched] [--missing]
                [query ...]

List albums (or files) of the library. Terms are ANDed; a lone `,` means or; `year-`
or `title+` sorts.

positional arguments:
  query            terms such as series:nains author:uderzo year:2010..2015
                   ^genre:humour

options:
  -h, --help       show this help message and exit
  --files          list files instead of albums
  --format FORMAT  output template, e.g. '$series T$vol $title'
  --limit N        at most N results (after sorting: `year-` puts the newest first)
  -c, --count      print only the number of results
  -l, --long       show everything known about each result (with --json: the complete
                   records)
  --json           print results as JSON
  --unmatched      list files that have no album (skipped, failed or not linked);
                   terms filter the path
  --missing        list registered files that are no longer on disk (moved or deleted
                   outside bdnex)

fields: any, attr, author, colorist, complete, corrupted, cover, coverartist,
embedded, fileid, filename, filepages, files, format, genre, id, inker, isbn, kind,
language, letterer, md5, metadata, pages, partial, path, penciller, publisher, rating,
review, series, size, source, spare, staged, summary, tagged, title, url, volume,
writer, year

Examples:
  bdnex ls series:nains                                  the albums of a series
  bdnex ls author:uderzo year:2010..2015 ^genre:humour   terms are ANDed; `^` negates; `a..b` is a range
  bdnex ls complete:true -c                              only count them
  bdnex ls --unmatched                                   files that have no album
  bdnex ls --missing                                     files the library knows that are no longer on disk
  bdnex ls series:nains --format '$series T$vol $title'  choose what is printed
```

### `bdnex series`

What you have of each series, and what is missing.

```text
usage: bdnex series [-h] [--missing] [-l] [--json] [name ...]

``bdnex series``: what you have of each series, and what is missing.

positional arguments:
  name        words of the series name (case and accents ignored)

options:
  -h, --help  show this help message and exit
  --missing   only the series that lack albums
  -l, --long  list the missing albums, with their address
  --json      print the result as JSON

The albums a series has come from its page, read when one of its albums is fetched
(`bdnex import --fetch`). Until then only what you own is known.

Examples:
  bdnex series                     what you have of each series
  bdnex series --missing           only the series that lack albums
  bdnex series nains --missing -l  the missing albums of one series, with their address
```

### `bdnex stats`

How much the library holds: albums read or only matched, files, formats, sizes.

```text
usage: bdnex stats [-h] [--json] [query ...]

How much the library holds: albums (read or only matched), files, formats, sizes.

positional arguments:
  query       only these albums and files, same terms as `bdnex ls`

options:
  -h, --help  show this help message and exit
  --json      the numbers as JSON

Without a query the whole library; with one (the terms of `bdnex ls`) only the albums
and files it matches. Read-only, no file is touched.

Examples:
  bdnex stats               how much the library holds
  bdnex stats series:nains  the same for a query
```

### `bdnex fields`

The fields a query can use, with their kind.

```text
usage: bdnex fields [-h]

List the fields a query can use, with their kind.

options:
  -h, --help  show this help message and exit

Use them as `field:value` in `bdnex ls`, `review`, `write`, `komga`... (see the page
*Querying the library*). `year-` or `title+` sorts.

Examples:
  bdnex fields  the fields a query can use (`series:`, `author:`, `year:`...), with their kind
```

### `bdnex duplicates`

The same book in several files.

```text
usage: bdnex duplicates [-h] [--covers] [--workers N]

``bdnex duplicates``: the same book in several files.

options:
  -h, --help   show this help message and exit
  --covers     also compare the first pages of every file (reads each file once; slow
               on a big library)
  --workers N  files read with --covers at once (0: the cores less one; 1: one by
               one); default: `import.workers`

Groups files with the same content, several files for one album (not marked as spare
versions), and files whose first page looks alike. It only reports: `bdnex import`
asks what to do about a duplicate when it meets one, and `bdnex trash` holds what was
replaced.

Examples:
  bdnex duplicates           the same book in several files, found by name and size
  bdnex duplicates --covers  also compare the first pages (reads every file: slow on a big library)
```

## Change the library

### `bdnex edit`

Change the metadata of albums or series by hand (kept across re-imports).

```text
usage: bdnex edit [-h] [--set KEY=VALUE] [-y] [-n] query [query ...]

``bdnex edit``: change the metadata of albums or series by hand.

positional arguments:
  query            which albums, as in `bdnex ls` (e.g. series:nains)

options:
  -h, --help       show this help message and exit
  --set KEY=VALUE  change a field of every matching album without opening the editor;
                   repeatable. Lists are separated by ';': --set genres='Humour;
                   Fantasy' --set credits.Writer='A; B'
  -y, --yes        do not ask for confirmation
  -n, --pretend    show what would change; change nothing

Afterwards, `bdnex write <same query>` puts the new values into the files.

Examples:
  bdnex edit series:nains                            change the albums in your editor
  bdnex edit series:nains --set publisher=Soleil -n  preview a change to a field, without the editor
```

### `bdnex mark`

Put your own marks on files or albums (checked=keep), to find them again with attr:.

```text
usage: bdnex mark [-h] [--set NAME[=VALUE]] [--remove NAME] [--albums] [-y] [-n]
                  query [query ...]

``bdnex mark``: put your own marks on files or albums (``checked=keep``,
``read=yes``), and take them off.

positional arguments:
  query               which files, as in `bdnex ls --files` (e.g. series:nains)

options:
  -h, --help          show this help message and exit
  --set NAME[=VALUE]  put this mark (lower case letters, digits and _); repeatable
  --remove NAME       take this mark off; repeatable
  --albums            mark the albums the query matches, not the files
  -y, --yes           do not ask for confirmation
  -n, --pretend       show what would change; change nothing

A mark is a name and a text value that bdnex keeps with a file (or an album, with
--albums) and never changes by itself. Find them again with `attr:NAME` or
`attr:NAME=VALUE` in any query, e.g. `bdnex ls --files attr:checked=keep`, or `bdnex
candidates --low ^attr:checked` for the files nobody looked at. They are not written
into the files.

Examples:
  bdnex mark series:nains --set checked=keep  put your own mark on the files of a query
  bdnex mark series:nains --remove checked    take it off
  bdnex ls --files attr:checked               find them again
```

### `bdnex reset`

Empty the library (albums, files, edits), keep the downloaded sitemaps and pages.

```text
usage: bdnex reset [-h] [-n] [-y] [--no-backup]

``bdnex reset``: empty the library, keep what was downloaded.

options:
  -h, --help     show this help message and exit
  -n, --pretend  show what would be deleted; change nothing
  -y, --yes      do not ask for confirmation
  --no-backup    do not copy the database to <library>.before-reset-<date> first

Deletes the files, albums, series, credits, genres and publishers recorded in the
library, with your `bdnex edit` changes and the import history. Kept: the sitemaps and
every album / series page and cover already fetched, so the next import needs no
network for them. Your comic files are not touched.

Examples:
  bdnex reset -n  preview: what would be deleted (the albums, files and edits; sitemaps and pages are kept)
  bdnex reset     empty the library, after copying it to `<library>.before-reset-<date>`
```

### `bdnex update`

Read the files again after another tool changed them (the opposite of write).

```text
usage: bdnex update [-h] [--all] [-n] [--workers N] [query ...]

``bdnex update``: read the files again, for what was changed outside bdnex.

positional arguments:
  query          only these files, same terms as `bdnex ls --files`

options:
  -h, --help     show this help message and exit
  --all          read every file of the query, not only the ones that changed
  -n, --pretend  show what would be read again; change nothing
  --workers N    files read at once (0: the cores less one; 1: one by one); default:
                 `import.workers`

The opposite of `bdnex write`: where `write` puts the library's metadata into the
files, `update` records what the files are now (size, checksum, pages, damage, their
own ComicInfo.xml) after you changed them with another tool. By default only the files
whose size or date differs from what was recorded are read; --all reads every file of
the query. The albums are not touched, and no file is changed. A file that is gone is
listed (`bdnex relocate --find` finds moved files).

Examples:
  bdnex update -n                  which files changed since they were read (after another tool changed them)
  bdnex update --all series:nains  read every file of a series again, changed or not
```

### `bdnex check`

Is the library consistent with itself and the disk? (--matches: do the matches look right?).

```text
usage: bdnex check [-h] [--matches] [--fix]

``bdnex check``: is the library consistent with itself and with the disk?

options:
  -h, --help  show this help message and exit
  --matches   also weigh every match against its album's page (the file's own tags,
              volume, year, authors, its folder) and list the ones that look wrong;
              with --fix they are flagged for `bdnex review`
  --fix       settle what is safe to settle (listed as [fixable])

Looks at the database (integrity, links between tables), at the files it points to
(missing, changed), at covers, the trash and leftovers of interrupted writes. Nothing
is changed unless you add --fix, which only settles what is safe: empty series, dead
trash records, covers that are gone, temporary files (and, with --matches, flags the
doubtful matches for `bdnex review`).

Examples:
  bdnex check            is the library consistent with itself and the disk?
  bdnex check --matches  also weigh every match against its album's page
  bdnex check --fix      settle what is safe to settle
```

## Change the files

### `bdnex write`

Write ComicInfo.xml into files (converts CBR/PDF to CBZ).

```text
usage: bdnex write [-h] [-n] [--workers N] [--force] [-v] [--log-file PATH]
                   [query ...]

Write ComicInfo.xml into the files where they are. CBR and PDF files are converted to
CBZ next to the original (see write.cbr / write.pdf).

positional arguments:
  query            files to process, same terms as `bdnex ls --files`

options:
  -h, --help       show this help message and exit
  -n, --pretend    show what would be done; change nothing
  --workers N      files written at once (0: the cores less one, at most 4; 1: one by
                   one); default: `write.workers`
  --force          never ask: the database wins, name clashes get a number, originals
                   are kept
  -v, --verbose    more detail: -v what is decided and why (every file), -vv how
                   (reads, cache, requests, threads), -vvv everything, libraries and
                   SQL included
  --log-file PATH  also write everything, at the debug level and with the time, to
                   this file (for a long run)

Examples:
  bdnex write -n            preview: which files would get a ComicInfo.xml
  bdnex write series:nains  write the tags into the files of one series
```

### `bdnex move`

Write tags and rename files into the library tree (--copy keeps the originals).

```text
usage: bdnex move [-h] [-n] [--workers N] [--force] [--copy] [-v] [--log-file PATH]
                  [query ...]

Write ComicInfo.xml and rename the files into the library tree (`collection_path`,
`paths` in the config).

positional arguments:
  query            files to process, same terms as `bdnex ls --files`

options:
  -h, --help       show this help message and exit
  -n, --pretend    show what would be done; change nothing
  --workers N      files written at once (0: the cores less one, at most 4; 1: one by
                   one); default: `write.workers`
  --force          never ask: the database wins, name clashes get a number, originals
                   are kept
  --copy           leave the source files untouched; the copy is what the database
                   tracks
  -v, --verbose    more detail: -v what is decided and why (every file), -vv how
                   (reads, cache, requests, threads), -vvv everything, libraries and
                   SQL included
  --log-file PATH  also write everything, at the debug level and with the time, to
                   this file (for a long run)

Examples:
  bdnex move -n                   preview: where each file would go in the collection
  bdnex move series:nains         write tags and rename the files into the tree
  bdnex move --copy series:nains  copy them in and leave the originals untouched
```

### `bdnex repair`

Rebuild damaged archives (wrong page checksums) as clean CBZ files.

```text
usage: bdnex repair [-h] [-n] [--workers N] [--force] [-v] [--log-file PATH]
                    [query ...]

Rebuild damaged archives (a page whose checksum is wrong but whose data is intact) as
a clean CBZ. Only files marked corrupted are touched; ComicInfo.xml and pages are kept
as they are.

positional arguments:
  query            files to process, same terms as `bdnex ls --files`

options:
  -h, --help       show this help message and exit
  -n, --pretend    show what would be done; change nothing
  --workers N      files written at once (0: the cores less one, at most 4; 1: one by
                   one); default: `write.workers`
  --force          never ask: the database wins, name clashes get a number, originals
                   are kept
  -v, --verbose    more detail: -v what is decided and why (every file), -vv how
                   (reads, cache, requests, threads), -vvv everything, libraries and
                   SQL included
  --log-file PATH  also write everything, at the debug level and with the time, to
                   this file (for a long run)

Examples:
  bdnex repair -n  preview: which archives are damaged
  bdnex repair     rebuild them as clean CBZ files (the damaged one goes to the trash)
```

### `bdnex convert`

Convert the PDF files of the library to CBZ (next to the original).

```text
usage: bdnex convert [-h] [-n] [--workers N] [--force] [--from FORMAT] [-v]
                     [--log-file PATH]
                     [query ...]

Convert PDF (or CBR) files that are in the library to CBZ, next to the original, with
the ComicInfo.xml. What happens to the original follows write.delete_original. Only
files matched to an album are converted.

positional arguments:
  query            files to process, same terms as `bdnex ls --files`

options:
  -h, --help       show this help message and exit
  -n, --pretend    show what would be done; change nothing
  --workers N      files written at once (0: the cores less one, at most 4; 1: one by
                   one); default: `write.workers`
  --force          never ask: the database wins, name clashes get a number, originals
                   are kept
  --from FORMAT    the format to convert: pdf (the default) or cbr; repeat it for both
  -v, --verbose    more detail: -v what is decided and why (every file), -vv how
                   (reads, cache, requests, threads), -vvv everything, libraries and
                   SQL included
  --log-file PATH  also write everything, at the debug level and with the time, to
                   this file (for a long run)

Examples:
  bdnex convert -n          preview: which PDF files would become CBZ
  bdnex convert --from cbr  convert CBR files instead
```

### `bdnex undo`

Put back the files a `move` run renamed.

```text
usage: bdnex undo [-h] [-n] [batch]

``bdnex undo``: put back the files a `move` run renamed.

positional arguments:
  batch          the run to take back: its name from the list, or `last`

options:
  -h, --help     show this help message and exit
  -n, --pretend  show what would be moved back; change nothing

Every `bdnex move` (and `import --move`) run remembers the files it renamed or moved.
`bdnex undo` lists the runs; `bdnex undo last` or `bdnex undo <run>` moves its files
back to where they were, in the library too. What cannot be taken back: tags written
into a file (the file keeps them), a CBR or PDF that was converted (the original is in
the trash: `bdnex trash`), copies, and files that were changed or moved again since.

Examples:
  bdnex undo          list the runs of `bdnex move` you can take back
  bdnex undo last -n  preview taking back the last one
```

### `bdnex trash`

List, restore or empty the files that were replaced.

```text
usage: bdnex trash [-h] [--restore ID] [--place | --reimport] [--empty]
                   [--older-than DAYS] [-y] [-n]

``bdnex trash``: look at, restore or empty the files that were replaced.

options:
  -h, --help         show this help message and exit
  --restore ID       put that file back where it came from
  --place            with --restore: then move it to where its album belongs (as
                     `bdnex move` does)
  --reimport         with --restore: then match it again (you choose the album, the
                     old link is replaced) and move it into the library tree, as
                     `bdnex import --match --force --move` does
  --empty            delete the trashed files for good
  --older-than DAYS  with --empty: only what was trashed that long ago
  -y, --yes          do not ask for confirmation
  -n, --pretend      show what would be done; change nothing

When you choose to keep a new file instead of an existing one, the existing file is
moved here (to `trash.dir` in your library folder), never deleted. Emptying the trash
deletes the files and their records for good.

Examples:
  bdnex trash                             list the files that were replaced
  bdnex trash --restore 12                put one back where it came from
  bdnex trash --empty --older-than 30 -n  preview deleting what was trashed more than 30 days ago
```

## Files that moved

### `bdnex relocate`

Your whole comics folder moved: tell the library the new place.

```text
usage: bdnex relocate [-h] [--find] [--workers N] [-n] [OLD NEW | FOLDER ...]

``bdnex relocate``: your comics moved; tell the library (the whole folder: OLD NEW),
or find the moved files (--find).

positional arguments:
  OLD NEW | FOLDER  the folder the files used to be in, then the folder they are in
                    now; with --find: where to look (default: your collection,
                    `collection_path` in the configuration)

options:
  -h, --help        show this help message and exit
  --find            instead: look for the files the library knows but that are no
                    longer where they were (`bdnex ls --missing`) in the folders
                    given, by size and checksum, and link a single copy again, with
                    its album and history. Nothing on disk is touched
  --workers N       files read at once (0: the cores less one; with --find; 1: one by
                    one); default: `import.workers`
  -n, --pretend     show what would change; change nothing

Files under `collection_path` are stored relative to it: after moving the whole
collection, just change `collection_path`. A folder listed in `import.sources` is one
record in the library: `relocate OLD NEW` with its old path updates that record and
its files follow (then change the path in `import.sources` too). For other files
stored with their full path, `relocate` rewrites the stored paths. It touches nothing
on disk. `bdnex ls --missing` lists what is not found. To find individual files that
were moved or renamed, by their content, use `--find` (`bdnex relocate --find
FOLDER...`).

Examples:
  bdnex relocate /old/comics /new/comics  your whole folder moved: tell the library
  bdnex relocate --find -n ~/comics       files were moved by hand: find them again by their content
```

## Other programs

### `bdnex komga`

See what a Komga server holds for your files (show), or send it the library's metadata (push).

```text
usage: bdnex komga [-h] [--only {books,series}] [--fields LIST] [--no-lock]
                   [--with-review] [--json] [--missing] [-y] [-n] [-v]
                   [--log-file PATH]
                   {show,push} [query ...]

``bdnex komga``: see what a Komga server holds for your files, and send the library's
metadata to it.

positional arguments:
  {show,push}           show: what Komga holds for each file, next to the library's
                        value (reads only); push: send the library's metadata to Komga
  query                 only the files of these albums, same terms as `bdnex ls`:
                        format:pdf author:uderzo series:nains year:2010..2015

options:
  -h, --help            show this help message and exit
  --only {books,series}
                        push only the books, or only the series (default: both)
  --fields LIST         push only these fields, comma separated (the others are left
                        as Komga has them): title, number, releaseDate, authors,
                        summary, isbn, links, tags, series-title, publisher, language,
                        genres, series-summary, series-status, series-total, series-
                        link
  --no-lock             push: do not lock the fields sent (a Komga rescan may then
                        undo them); default: komga.lock
  --with-review         push: also send the files whose match waits for a look (`bdnex
                        review`); by default they are left out, so that a doubtful
                        match never puts wrong metadata (and a lock) in Komga
  --json                show: print what Komga holds as JSON
  --missing             show: list the Komga books that no library file matches
                        (instead of the matches)
  -y, --yes             push: do not ask for confirmation
  -n, --pretend         show what would be sent to Komga (push); change nothing
  -v, --verbose         more detail: -v what is decided and why (every file), -vv how
                        (reads, cache, requests, threads), -vvv everything, libraries
                        and SQL included
  --log-file PATH       also write everything, at the debug level and with the time,
                        to this file (for a long run)

Komga does not read the ComicInfo.xml inside a PDF, so `push` is how a PDF gets its
title, summary, credits and the rest in Komga. A Komga book is matched to a library
file by its file name and size (the folders may differ). No comic file is read or
changed.

Examples:
  bdnex komga show format:pdf      what Komga holds for these files, next to the library's values
  bdnex komga push -n              preview what would be sent
  bdnex komga push --fields title  send only some fields
```

### `bdnex serve`

Open the library in a browser (web page and JSON API; read-only unless you allow changes).

```text
usage: bdnex serve [-h] [--host HOST] [--port PORT] [--token TOKEN]
                   [--password-env NAME] [--allow-changes] [-v] [--log-file PATH]

Serve the library over HTTP: a JSON API (documented at /docs) and a web page at /,
read-only unless you allow changes. Needs `pip install 'bdnex[api]'`.

options:
  -h, --help           show this help message and exit
  --host HOST          address to listen on (default api.host: 127.0.0.1, this machine
                       only)
  --port PORT          port (default api.port: 8765)
  --token TOKEN        require `Authorization: Bearer <token>` (default api.token)
  --password-env NAME  the environment variable that holds the web page's password
                       (default BDNEX_PASSWORD; else api.password): the page then asks
                       for api.username (or $BDNEX_USER) and it
  --allow-changes      let the web page run jobs and change the library (default
                       api.allow_changes: false, read-only)
  -v, --verbose        more detail: -v what is decided and why (every file), -vv how
                       (reads, cache, requests, threads), -vvv everything, libraries
                       and SQL included
  --log-file PATH      also write everything, at the debug level and with the time, to
                       this file (for a long run)

Examples:
  bdnex serve                  open the library in a browser (read-only), at http://127.0.0.1:8765
  bdnex serve --allow-changes  let the page run tasks and change the library
```

## Setup

### `bdnex config`

Show or edit the configuration.

```text
usage: bdnex config [-h] [-p] [-e] [-u] [--clear] [-d]

Show or edit the bdnex configuration

options:
  -h, --help      show this help message and exit
  -p, --path      print the config file path
  -e, --edit      open the config file in $EDITOR
  -u, --update    add the options your file does not list yet, commented out (a .bak
                  copy is kept)
  --clear         show the secrets too (API keys, passwords, the fetcher address);
                  they are hidden by default
  -d, --defaults  print every option with its default

Examples:
  bdnex config           show the configuration in use (secrets hidden)
  bdnex config --edit    open it in your editor
  bdnex config --update  add the options your file does not list yet, commented out
```

### `bdnex aliases`

List your short commands, add one (--add) or remove one (--rm) in the configuration file.

```text
usage: bdnex aliases [-h] [--add "NAME = COMMAND  # WHY"] [--rm NAME] [--json] [-n]

``bdnex aliases``: list your short commands, add one or remove one (the configuration
file is edited for you).

options:
  -h, --help            show this help message and exit
  --add "NAME = COMMAND  # WHY"
                        add an alias (or replace the one with that name): a long name
                        that says what it does, the command with its options, and
                        after ` # ` what it is for; repeatable
  --rm NAME             remove an alias of yours, or switch off one of bdnex's;
                        repeatable
  --json                list them as JSON
  -n, --pretend         show what would change; change nothing

An alias is a name for a bdnex command and its options: `bdnex settle-doubtful-matches
-n` runs `bdnex review --auto -n` (what you type after the name is added). bdnex ships
some; yours are added to them (the same name replaces one). --add and --rm write the
`aliases:` list of your configuration file (the previous file is kept as `.bak`, your
comments elsewhere stay). Removing one of bdnex's writes `NAME =`, which switches it
off for you.

Examples:
  bdnex aliases                                                   list the aliases at work: bdnex's own, and yours
  bdnex aliases --add "show-unlinked = ls --unmatched  # files that have no album"
                                                                  add one to your configuration file
  bdnex aliases --rm show-unlinked                                remove it (or switch off one of bdnex's)
  bdnex aliases --add "x = ls" -n                                 preview a change
```

### `bdnex completion`

Print a tab-completion script for bash, zsh or fish.

```text
usage: bdnex completion [-h] {bash,zsh,fish}

Print a tab-completion script: commands, options and file names. Load it with `eval
"$(bdnex completion zsh)"` in your shell's startup file.

positional arguments:
  {bash,zsh,fish}

options:
  -h, --help       show this help message and exit

Examples:
  bdnex completion bash  print a tab-completion script (also zsh and fish)
```
