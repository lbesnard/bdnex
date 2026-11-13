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
| Paths relative to `collection_path`; `bdnex relocate` | available |
| `bdnex reset`: empty the library, keep sitemaps and fetched pages | available |
| Duplicates: side-by-side comparison, spare versions in `_versions/`, `bdnex trash` | available |
| Files from outside the library folder: `import.outside`, staged files | available |
| Cover check with SIFT (`bdnex[cover]`), cover hash for duplicates | available |
| Bédéthèque source plugin, pluggable page fetchers | available |
| Plugin system for other metadata sources | available |
| Keep every replaced `ComicInfo.xml`, with a restore command | planned |
| `bdnex backup` (database copy without the re-downloadable sitemaps) | planned |
| Folders as series: obvious ones assumed, the others asked, files mapped by volume | available |
| Every album of a series, kept with the series; `bdnex series` shows what is missing | available |
| Series genres, status and other series-page details | available, read without knowing the layout; to be checked against a real current series page |
| Cover similarity as part of the match score (today it is a check after the fetch) | undecided |
| Swap the main file and a spare version | planned |
| A second source (e.g. BnF) | planned |

See [Library database](dev/library.md) for what is stored.
