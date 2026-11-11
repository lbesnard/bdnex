# Writing tags and organising files *(planned)*

Metadata lives in the library database. `bdnex write` pushes it back into the
files as `ComicInfo.xml`, and optionally renames and moves them.

| Command | Effect |
|---|---|
| `bdnex write` | write tags, leave files where they are |
| `bdnex move` | write tags and rename into the library tree from the path templates |
| `--copy` | keep the source file untouched; the copy is registered in the database |
| `--pretend` | show old and new paths only |

## Conflicts

If a file already has a different `ComicInfo.xml`, bdnex shows the difference
and asks what to do. `--force` overwrites without asking.

## Formats

- `.cbz` is the only format bdnex writes.
- `.cbr` files are converted to `.cbz`, or skipped (`write.cbr: skip`).
- `.pdf` files are converted by extracting the embedded page images unchanged
  (no re-encoding), or rendered when a page is not a plain image.
- Before the original is replaced, page count and image bytes are verified.
- After every write the new checksum is stored in the database.

## Path templates

```yaml
paths:
  oneshot: 'OneShot/$language/$title - $authors/$title - $authors'
  series:  'Series/$series/$series - T$volume:02 - $title'
```

If a field used by a template is empty, bdnex asks for it, or uses a
placeholder (`paths.missing_field`).
