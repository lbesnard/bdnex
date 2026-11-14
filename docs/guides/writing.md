# Writing tags and organising files

The library database holds the metadata. Two commands push it into your files:

| Command | Effect |
|---|---|
| `bdnex write [query]` | write `ComicInfo.xml` where the files are; CBR and PDF files become a CBZ next to the original |
| `bdnex move [query]` | the same, then rename into the library tree (`collection_path` + `paths` in the config) |
| `bdnex convert [query]` | convert the PDFs (`--from cbr` also CBRs) of the library to CBZ, next to the original, whatever `write.pdf` says |
| `bdnex repair [query]` | rebuild damaged archives as clean CBZ files, nothing else |
| `bdnex move --copy [query]` | as `move`, but the source stays untouched; the **copy** is what the database tracks |

`query` selects files with the same terms as [`bdnex ls --files`](queries.md); only files already
matched to an album are processed (`bdnex import` first). Options: `-n/--pretend` shows what would
happen and changes nothing, `--force` never asks.

## What you see while it runs

Each file goes through the same steps, and a long run shows them:

1. its archive is **read** (a PDF page by page);
2. a **new CBZ is written** next to it: the pages byte for byte, plus `ComicInfo.xml`;
3. the new file is **checked** against the original pages, and made sure to be on the disk;
4. it is **put in place** (renamed into the collection, for `move`);
5. the original is **removed** (when it moved or was converted, and `write.delete_original` allows it), and the
   **library is updated**.

Nothing replaces the original until step 3 has passed. On a terminal a bar shows the file and the step it is at
(`Nains - T02.cbz · checking the new archive against the original pages`); as soon as a file is done its result appears
above the bar: what happened, where it went, the steps, how long it took. Without a terminal each result is just printed.
`-v` also logs every step of every file (and `bdnex undo` takes a `move` back).

### Several files at once

`write`, `move` and `repair` work on several files at the same time (`write.workers`, or `--workers N` for one run; `0` is the
cores less one, at most 4, and `1` is one by one). The bar then lists the files being worked on
(`file a.cbz · file b.cbz · file c.cbz  (3 at once)`) and the results still come in order. Files that need no answer from you
are written in worker processes; a file that needs a decision (a ComicInfo.xml that disagrees with the library, a name
that is taken, a missing value, a size that changed) is handed back and done in the main process, in its turn, with the
usual question, so a question never comes from a file that is half written. Files that would land on the same name are
never written at the same time. A preview is not parallel (it is instant). Every file is held in memory while it is worked
on, so with big PDFs use fewer workers.

## Previewing: `-n`

`bdnex write -n`, `move -n` and `repair -n` change nothing: no file is written, converted, moved or deleted, the
library is opened read-only, no question is asked, and the report ends with `Preview only`. Each file gets its
line:

```text
● would do  some file.cbz
    from  inbox/some file.cbz
    to    Series/Nains/Nains - T01 - Redwin.cbz
    • write ComicInfo.xml
```

A preview is quick because it does not read the pictures: it only looks at the CBZ's `ComicInfo.xml` and the
library. For a PDF it cannot say how many pages would be drawn; the real run reports that.

Paths are shown below the library folder; `from` (red) and `to` (green) only highlight the part that differs.
A file that already sits where it belongs says `stays where it is`, and `bdnex write -n` always does, because
`write` never renames (a CBR or PDF would become a `.cbz` beside it). Files that need nothing come out as
`unchanged`. Colour is off in a pipe or with `NO_COLOR`.

`bdnex import` can do it in one go: `-W/--write`, `--move` or `--copy`, or set `import.mode`
(`in_place`, `copy`, `move`) in the configuration.

## Taking a move back: `bdnex undo`

Every `bdnex move` (or `import --move`) run remembers the files it renamed or moved, under a name made of its date and
time.

```bash
bdnex undo            # the runs, newest first: how many files each moved
bdnex undo last -n    # preview: where each file would go back to
bdnex undo last       # move them back, in the library too (or `bdnex undo 20261114-093012`)
```

Files go back to the exact path they came from, and the folders the move had made are removed again. A file whose old
place has been taken by another file, or that is gone from where `move` put it, is skipped and reported.

What `undo` cannot take back: the tags that were written into a file (it keeps them; `write` changes a file in place
and does not keep the old one), a CBR or PDF that was converted (the original waits in the [trash](versions.md#the-trash),
`bdnex trash --restore`), and copies (`--copy` leaves the originals where they are).

## Folders left behind

After `move` (or `import --move`) the folder a file left is removed when only system leftovers (`.DS_Store`,
`Thumbs.db`, `desktop.ini`, `._*` files) remain in it, and so are the parents that this empties. Inside the
library folder that goes up to, never including, the library root. For a file taken from outside the library
(an inbox) only its own folder is removed, so a download folder is never climbed out of. A folder that still holds
anything else is left alone.

## What is written

`ComicInfo.xml` at the root of the CBZ, validated against the ComicInfo schema: title, series,
volume (as both `Number`, which readers sort on, and `Volume`), writer, penciller, colorist, inker,
letterer, cover artist, publisher, genre, year/month/day, summary, language, web page, community
rating, the **format**, the real page count, and in `Notes` the ISBN and the edition note of the source
(e.g. *Noté "Première édition". Format 210 x 283 mm. Avec une carte…*). A one-shot uses its title as `Series`.

After every write the file's checksum is recomputed and stored in the database.

## Safety

- The new archive is built in a temporary file next to its destination and **verified** (same pages,
  byte for byte, ComicInfo present) before it replaces or sits next to anything.
- If verification fails, the original is left untouched and no temporary file remains.
- The checks are: the zip's own checksums, every page present with the same bytes as in the original (a
  hash of each), `ComicInfo.xml` as intended. The new file is flushed to disk before it takes its place.
- **Before an original CBR or PDF is deleted**, the new CBZ is read again from disk and checked a second time;
  if that fails the new file is removed, the original is kept and the file is reported as `failed`.
- **Size check.** The new archive must also have about the size of the original: more than
  `write.size_tolerance` percent apart (default 20, and never for a difference under 1 MiB) means something
  unexpected happened, for instance a PDF whose pages had to be rendered to PNG. `write.size_change` then
  decides: `ask` (default; shows both sizes and lets you refuse or accept), `accept` or `refuse`. Refusing
  leaves the original untouched. Without a terminal to ask on, `ask` refuses; `--force` accepts.
  `write.size_tolerance: 0` turns the check off. A converted file's report line gives both sizes.
- One failing file never stops the batch.

## Damaged archives

`bdnex import` opens every archive. A file that cannot be opened at all is reported as `failed`. A
file that opens but has a page failing its checksum is still **matched and recorded**, marked
*corrupted*: `bdnex ls --files corrupted:true` lists them.

`bdnex write` and `bdnex move` try to repair such a file first, and `bdnex repair [query]` does only
that. The repair recovers a page whose stored checksum is wrong while its data is complete (the right
size, and a picture that decodes), then rebuilds the archive with correct checksums. It changes no
page and keeps the file's `ComicInfo.xml`, and the new archive is verified before it replaces the
old one.

If a page cannot be recovered (its data is truncated or is not an image), nothing is changed and
bdnex asks what to do: **leave** the file as it is, or **move it anyway**, as it is and without
tags (`move` and `--copy` only). `write.corrupted: ask | leave | move` decides in advance; `--force`
and `--pretend` never ask (`--force` leaves it, `--pretend` says you would be asked).

## Stopping a run

Every question of `bdnex write`, `move` and `repair` ends with **[A]bort the run** (press `A`). What was
already done stays done, the file being asked about and the ones after it are not touched, and the report
ends with `Aborted at <file>`. Run the same command again to continue: finished files come out as `unchanged`.
Ctrl-C works too.

## Conflicts

If the file already has a `ComicInfo.xml` that differs from the database, bdnex shows the
differences and asks which wins (`write.conflict: ask`). The differences are shown like a `git diff`:

```text
  ~ Writer
      - Wilfrid Lupano          (red: in the file)
      + Lupano, Wilfrid         (green: in the database)
  + CommunityRating  4.20       (only in the database)
```

The database wins where it has a value. A field it has no value for is **never erased**: if the
file says `Format: One Shot` and the library has no format, the file keeps it, and it is not listed
as a difference. When nothing is left to decide, nothing is asked and the file is not rewritten.

Only the words that changed are bold, newlines are folded, and a long value such as a summary is cut
around its first difference with its length before and after. Colours appear on a terminal only;
`NO_COLOR=1` turns them off and `FORCE_COLOR=1` forces them. Set it to `database` or `file` to decide
once, or use `--force` (the database wins where it has a value).

The question offers five answers: **Database**, **Database wins for the rest of the queue** (`W`: this file and every
later conflict of the run, with no more questions; the next run asks again), **File**, **Field by field** and
**Skip this file**. To never be asked, set `write.conflict: database`.
*Field by field* goes through the differences one at a time: take the database's value, the file's, or type
your own (a summary, a list of writers separated by commas, a year...).

**What you choose is kept in the library.** Whatever won over the database's value is stored as a hand edit
of the album (the same as `bdnex edit`), so the library says what the file now says, and a later
`bdnex import --recache` or `bdnex reparse` keeps it. The page fetched from the source is not touched: it
stays stored as it was, and the edits are laid over it. `Series` is never changed this way (it would rename
the series of every album; use `bdnex edit` for that), nor are the page count, rating, notes and web address,
which the library does not take from a file. Nothing is stored in a preview (`--pretend`).

## Formats

- `.cbz` is the only format bdnex writes. `write.format_preferred` says what to do with the others:
  - `cbz` (default): CBR and PDF files are converted to CBZ, each one only if its own switch allows it
    (`write.cbr`: `convert` or `skip`; `write.pdf`: `convert`, `tag` or `skip`), so you can convert CBRs and keep PDFs, or the reverse.
  - `none`: nothing is ever converted. A CBR or a PDF cannot be tagged as it is, so `write`, `move` and `repair`
    leave it alone and say so; CBZ files are tagged and moved as usual.
  A CBR target (`cbr`) is not offered: writing RAR needs the proprietary `rar` program.
- `.cbr` cannot be written (RAR is proprietary): it is converted to CBZ (`write.cbr: convert`), or
  left alone (`skip`).
- `.pdf`: pages are taken **one by one**. A page that is a single picture (or one picture plus tiny ones such as a
  logo, under 2% of the page) is extracted untouched: no re-encoding, no quality loss, about the PDF's own size.
  If that picture is in a format comic readers do not open (JPEG 2000, JBIG2...), it is decoded and saved as PNG
  with the same pixels: still lossless, but bigger than the compressed original.
  Only the pages that are something else (text, several large pictures, transparency, a picture format viewers
  do not open) are **drawn**: bdnex makes a new picture of the page, at `write.pdf_dpi` at most and never sharper than the
  page's own pictures. This is **not lossless**: a drawn page is a new picture, not a copy of the PDF's. A scanner's
  layered PDF (a JPEG background with a 1-bit text layer) is made only of such pages. Photographic pages are saved as
  JPEG (`write.pdf_jpeg_quality`, 90 by default; raise it for a closer copy at a bigger size) and the others as PNG.
  To keep the PDF itself, see `write.pdf: tag` and `skip` below.
  `write.pdf_mode: extract` refuses a PDF with such pages and names them, `render` draws every page.
  `bdnex write --pretend` tells you beforehand: `all 120 pages extracted untouched`, or
  `3 of 120 page(s) had to be drawn (page 4, 57, 58)`. A PDF that comes out many times bigger than it went in
  was drawn in PNG: check that line, and the size check below catches it.
- **Keeping a PDF a PDF: `write.pdf`.** `convert` (the default) makes a CBZ. `tag` keeps the PDF and writes the tags
  **into it**: the title, writer, summary and keywords in its Info dictionary (what viewers show), an XMP packet
  (Dublin Core, plus the series and its number as Calibre keeps them), and the same `ComicInfo.xml` as a CBZ,
  attached to the PDF so that bdnex can read it back (a second `write` says `unchanged`; a conflict is asked
  like for a CBZ). The pages are not touched: the file is saved incrementally, the tagged copy must begin with the
  original's exact bytes and is checked before it replaces the original, and `move` puts it in the tree as a `.pdf`
  with your path templates. The XMP packet it replaces, if any, is lost. An encrypted PDF, or one whose structure is
  damaged, can not be tagged: `move` still moves it, as it is. `skip` leaves the PDF alone entirely: no tags,
  and `move` only renames it. Other readers use what they can: Info and XMP are read by most PDF viewers and by
  Calibre; few comic servers read PDF tags, so check yours (not tested here with Komga or Kavita).
- Conversion is automatic with `bdnex write` and `bdnex move`: set `write.cbr` to `convert` (the default) or `skip`,
  and `write.pdf` to `convert` (the default), `tag` or `skip`. The other direction, CBZ to CBR, is not offered: RAR can not be written.
- **Converting later: `bdnex convert [query]`.** Keep your PDFs as they are at first (`write.pdf: tag` or `skip`),
  and convert them whenever you decide: `bdnex convert -n` previews, `bdnex convert` converts every PDF matched to
  an album, `bdnex convert series:nains` only some of them, `--from cbr` (repeatable: `--from pdf --from cbr`)
  picks the format. The CBZ is written next to the original with the same checks as `write`, and the original
  follows `write.delete_original` (asked, kept or sent to the trash). It does not move files: run `bdnex move`
  afterwards to put them in the tree. Nothing is converted when `write.format_preferred` is `none`. Find what is left
  with `bdnex ls --files format:pdf`.
- `write.delete_original: ask | yes | no` decides what happens to the CBR/PDF after conversion. `yes` deletes it
  without asking, once the new CBZ has passed both checks. With `write.use_trash: true` (the default) "deleted"
  means moved to the trash folder (`trash.dir`, inside your library folder) and recorded in the library with its
  album, checksum and the reason: `bdnex trash` lists it, `--restore ID` puts it back, `--empty` deletes it for
  good. `use_trash: false` deletes the original for good, straight away. A damaged original that `bdnex repair`
  replaced follows the same rule.
- **Free space.** Before a file is written, the disk that holds it must keep `write.min_free_space` MiB free
  after the new archive (estimated at 125% of the original), and so must the disk of the library database, so a
  full disk can neither leave a half-written archive nor starve the database. If not, the file is reported as
  `failed` and nothing is written. If the disk fills up anyway, the partial file is removed and the original is
  untouched (SQLite itself rolls back an interrupted change). `write.min_free_space: 0` turns the check off.

## Path templates

```yaml
paths:
  oneshot: 'OneShot/$language/$title - $authors/$title - $authors'
  series:  'Series/$series/$series - T$volume:02 - $title'
```

- `$field` or `${field}`; `$volume:02` zero-pads a number. A special such as *Hors-série* is used
  as `$volume` when there is no number.
- Fields: `kind series title volume language year publisher isbn genre authors writer penciller colorist`.
- Every value is made safe for file names (`/ \ : * ? " < > |` become `_`), so a value can never
  create a folder. The extension `.cbz` is added for you.
- If a name already exists, bdnex shows both files and asks which to keep: the existing one, the new one (the existing goes to the [trash](versions.md#the-trash)), or both (the new one becomes a spare version in `_versions/`, numbered if there are several). `--force` keeps both.

### Missing values

When a field used by the template is empty, `paths.missing_field` decides:

| Value | Behaviour |
|---|---|
| `ask` | ask for it, and **save the answer to the library** (and into the file) |
| `placeholder` | use `paths.placeholder` (default `Unknown`) |
| `skip` | leave that file alone |

Without a terminal (or with `--force`, or `--pretend`) `ask` falls back to the placeholder.
