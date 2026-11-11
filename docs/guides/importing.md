# Importing *(planned)*

`bdnex import <path>` is the main command, modelled on `beet import`. For every
file it looks for the matching Bédéthèque album, scores the candidates, and
stores the result in the library database.

## What happens

1. **Scan**: comic files (`.cbz`, `.cbr`, `.pdf`) are found, hashed (md5) and
   checked for corruption.
2. **Match**: the file name is fuzzy-matched against the locally cached
   sitemaps. This is fast and runs in parallel.
3. **Fetch**: album and series pages and covers are downloaded one at a time,
   with a polite delay, and cached in the database. Nothing is fetched twice.
4. **Score**: title similarity, cover similarity, volume and ISBN are combined
   into a distance. Below the *strong* threshold the match is applied
   automatically; between thresholds bdnex asks you; above, the file is skipped
   or you search by hand.
5. **Duplicates**: if the album is already in the library you choose to skip,
   keep both, replace, or merge.

## Options

| Option | Effect |
|---|---|
| `--pretend` | show what would happen, change nothing |
| `--recache` | refetch pages instead of using the cached copy |
| `--force` | never ask: apply the best match |
| `--copy` / `--move` / `--in-place` | what to do with the file |

All of them can be given defaults in the [configuration](configuration.md).
