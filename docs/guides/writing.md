# Writing tags and organising files

The library database holds the metadata. Two commands push it into your files:

| Command | Effect |
|---|---|
| `bdnex write [query]` | write `ComicInfo.xml` where the files are; CBR and PDF files become a CBZ next to the original |
| `bdnex move [query]` | the same, then rename into the library tree (`directory` + `paths` in the config) |
| `bdnex move --copy [query]` | as `move`, but the source stays untouched; the **copy** is what the database tracks |

`query` selects files with the same terms as [`bdnex ls --files`](queries.md); only files already
matched to an album are processed (`bdnex import` first). Options: `-n/--pretend` shows what would
happen and changes nothing, `--force` never asks.

`bdnex import` can do it in one go: `-W/--write`, `--move` or `--copy`, or set `import.mode`
(`in_place`, `copy`, `move`) in the configuration.

## What is written

`ComicInfo.xml` at the root of the CBZ, validated against the ComicInfo schema: title, series,
volume (as both `Number`, which readers sort on, and `Volume`), writer, penciller, colorist, inker,
letterer, cover artist, publisher, genre, year/month/day, summary, language, web page, community
rating, the ISBN (in `Notes`) and the **real** page count. A one-shot uses its title as `Series`.

After every write the file's checksum is recomputed and stored in the database.

## Safety

- The new archive is built in a temporary file next to its destination and **verified** (same pages,
  byte for byte, ComicInfo present) before it replaces or sits next to anything.
- If verification fails, the original is left untouched and no temporary file remains.
- One failing file never stops the batch.

## Conflicts

If the file already has a `ComicInfo.xml` that differs from the database, bdnex shows the
differences and asks which wins (`write.conflict: ask`). Set it to `database` or `file` to decide
once, or use `--force` (the database wins). Fields bdnex does not manage are kept.

## Formats

- `.cbz` is the only format bdnex writes.
- `.cbr` cannot be written (RAR is proprietary): it is converted to CBZ (`write.cbr: convert`), or
  left alone (`skip`).
- `.pdf`: `write.pdf_mode: auto` extracts the embedded page images **unchanged** when every page is a
  single plain image (no re-encoding, no quality loss); otherwise pages are rendered to PNG at
  `write.pdf_dpi`. `extract` refuses anything that is not extractable, `render` always renders.
- `write.delete_original: ask | yes | no` decides what happens to the CBR/PDF after conversion.

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
- If a name already exists, bdnex asks (skip, keep both with a number, or replace); `--force` keeps both.

### Missing values

When a field used by the template is empty, `paths.missing_field` decides:

| Value | Behaviour |
|---|---|
| `ask` | ask for it, and **save the answer to the library** (and into the file) |
| `placeholder` | use `paths.placeholder` (default `Unknown`) |
| `skip` | leave that file alone |

Without a terminal (or with `--force`, or `--pretend`) `ask` falls back to the placeholder.
