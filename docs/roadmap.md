# Roadmap

| Area | Status |
|---|---|
| Packaging with uv, wheel releases | available |
| Library database (files, albums, series, credits, stored pages) | available |
| `bdnex init`: download / refresh the album list, with a progress bar | available |
| `bdnex import`: `--match` / `--fetch` steps, review or `--auto`, background matching, duplicates, resume, remembered skips | available |
| Matching with folder names, series + volume, tolerant of authors / years in file names | available |
| `bdnex ls`: queries, `--unmatched`, `--missing` | available |
| `bdnex write` / `move`: tags, rename, convert CBR/PDF, repair damaged archives | available |
| `bdnex reparse`: rebuild albums from the stored pages, offline | available |
| `bdnex edit`: change metadata by hand, kept across re-imports | available |
| Paths relative to `directory`; `bdnex relocate` | available |
| `bdnex reset`: empty the library, keep sitemaps and fetched pages | available |
| Bédéthèque source plugin, pluggable page fetchers | available |
| Plugin system for other metadata sources | available |
| Keep every replaced `ComicInfo.xml`, with a restore command | planned |
| `bdnex backup` (database copy without the re-downloadable sitemaps) | planned |
| Cover similarity as part of the match score | undecided |
| A second source (e.g. BnF) | planned |

See [Library database](dev/library.md) for what is stored.
