# Duplicates, versions and the trash

An album can have several files: the same book as CBZ and CBR, a scan and a digital release, a copy
you downloaded twice. bdnex never decides this silently. It shows the files side by side, suggests one,
and lets you keep one, the other, or both.

## What counts as a duplicate

| Situation | How bdnex notices |
|---|---|
| exact copy | same checksum, whatever the name |
| same book, other container | same album, same page count and the same cover (CBZ, CBR and PDF of one book give nearly the same cover hash) |
| another version or edition | same album (or the same series and volume, `duplicates.keys`) with a different page count or cover |

## The question

When a second file turns up for an album that already has one, during `bdnex import` or when `bdnex move`
finds the destination name taken, you see both files:

```text
Nains - T01 - Redwin de la Forge.cbr
This album already has a file in the library.
  existing  Series/Nains/Nains - T01 - Redwin de la Forge.cbz
            cbz · 48 pages · 298.0 MB · ok
  new       /home/me/Downloads/Nains - T01 - Redwin de la Forge.cbr
            cbr · 48 pages · 312.4 MB · ok

  same book: same page count and the same cover
  suggested: keep the existing file (cbz is preferred to cbr (duplicates.prefer))

? Which one do you keep?
❯ Keep the existing file (the new one is not imported)  <- suggested
  Keep the new file (the existing one goes to the trash)
  Keep both (the new one becomes a spare version)
```

The suggestion prefers a readable file, then more pages, then your format order (`duplicates.prefer`,
`cbz, cbr, pdf` by default). On a tie it keeps what you already have.

- **Keep the existing file**: the new one is not imported and stays where it is.
- **Keep the new file**: the existing one goes to the [trash](#the-trash), never deleted.
- **Keep both**: the new one becomes a *spare version*.

You can decide in advance with `duplicates.action: skip | keep_both | replace` (`ask` is the default).
`--auto=all` and `--pretend` never ask: a duplicate is skipped.

## Spare versions

Each album has one **main** file, in your library tree where the path templates put it. Every other
file for the same album is a **spare version**, kept in `duplicates.versions_dir` (`_versions` by
default) inside your library folder, under the same sub-folders and name. A second spare version gets a
number, then a third:

```text
Series/Nains/Nains - T01 - Redwin de la Forge.cbz
_versions/Series/Nains/Nains - T01 - Redwin de la Forge.cbr
_versions/Series/Nains/Nains - T01 - Redwin de la Forge (2).cbz
```

So a reader such as Komga, pointed at your library folder minus `_versions`, sees one book. Spare
versions stay in the library: `bdnex ls --files spare:true` lists them, and `bdnex move` puts a spare
version back into `_versions` and a main file back in the tree. Tags are written to all of them.

## Fixing a bad import

- **A file was linked to the wrong album:** `bdnex import --match --force --move path/to/file` asks again, relinks
  the file and moves it where the new album belongs (the old folder is removed if it is left empty).
- **The duplicate question sent the wrong file to the trash:** `bdnex trash` lists it; `bdnex trash --restore ID` puts
  it back where it was and registers it. Add `--place` to move it to where its album belongs, or `--reimport` to also
  choose its album again (the same as the line above, on the restored file).
- **A whole folder was skipped by mistake:** `bdnex import --force folder` looks at the skipped files again.
- **Metadata is wrong, the match is right:** [`bdnex edit`](maintaining.md), then `bdnex move`.

## The trash

Whatever is replaced goes to `trash.dir` (`.bdnex-trash` in your library folder), in a dated sub-folder,
and the library keeps a record: where the file was, which album it belonged to, its checksum, size,
format and why it was trashed. Scans of your library folder never look inside hidden folders.

```bash
bdnex trash                        # list: id, date, size, original path, reason
bdnex trash --restore 3            # put it back (beside the original name if that is taken) and register it
bdnex trash --restore 3 --place    # restore it, then move it where its album belongs (as `bdnex move`)
bdnex trash --restore 3 --reimport # restore it, match it again (you pick the album) and move it into the tree
bdnex trash --empty -n             # preview: how many files, how much space
bdnex trash --empty                # delete them for good, with their records (asks first)
bdnex trash --empty --older-than 30 -y
```

`bdnex reset` empties the library but leaves the trash and its records alone; `bdnex trash --empty`
deletes them. Without a library folder in the configuration, the trash sits next to the database.

## Settings

```yaml
duplicates:
  keys: [series, volume]       # what makes two albums the same edition: series | volume | title
  action: ask                  # ask | skip | keep_both | replace
  prefer: [cbz, cbr, pdf]      # the format suggested first when the files are the same book
  versions_dir: _versions      # spare versions, inside `collection_path`

trash:
  dir: .bdnex-trash            # replaced files, inside `collection_path`
```
