# Importing

`bdnex import <path>...` matches comic files (`.cbz`, `.cbr`, `.pdf`) to Bédéthèque
albums and records the result in the library database. It does **not** modify your
files: writing tags and moving files is a separate step (see [Writing](writing.md)).

```bash
bdnex import ~/comics            # a folder, recursively
bdnex import album.cbz other.cbz
bdnex import -n ~/comics         # --pretend: show matches, change nothing
```

## What happens, per file

1. **Already imported?** A file linked to an album is skipped (`import.resume`),
   unless you pass `--force`.
2. **Readable?** A corrupted archive is reported as `failed`; the rest of the batch continues.
3. **Identical file?** Same checksum as a file already in the library: handled by the
   duplicate action (below).
4. **Match.** The file name is fuzzy-matched against the locally cached sitemaps (no
   network). `T01`, `Tome 1`… in the name are understood, and a matching volume number
   raises the score, a different one lowers it.
5. **Decide.** Score at or above `match.strong_threshold`: applied. Between
   `medium_threshold` and `strong_threshold`: you choose among the candidates (or skip).
   Below: you can still pick a candidate or skip. With `--force` nothing is asked:
   strong and medium matches are applied, the rest skipped.
6. **Same album, other file?** `duplicates.action` decides: `ask`, `skip`,
   `keep_both` or `replace` (the new file becomes the album's file; the old file stays
   on disk, unlinked).
7. **Metadata.** The album page is read through the cache. If it cannot be fetched
   (blocked, or no saved page), the album is stored from the **sitemap URL alone**
   (series, volume, title, one-shot or series) and reported as `partial` with the URL to
   save. A later import with the page available upgrades it in place.

Each run ends with a summary such as `3 file(s): 2 tagged, 1 partial`.

## Options

| Option | Effect |
|---|---|
| `-n`, `--pretend` | match and report only; no database change, no page fetched |
| `--force` | never ask; re-import known files |
| `--recache` | refetch the sitemaps and album pages instead of using the cache |

Defaults for thresholds and duplicate handling live in the
[configuration](configuration.md) (`match:`, `duplicates:`, `import:`).
