# Configuration

## The file

bdnex reads `~/.config/bdnex/bdnex.yaml` (or `$XDG_CONFIG_HOME/bdnex/bdnex.yaml`,
or the file named by `$BDNEX_CONFIG`).

On the **first launch** bdnex creates the file there and tells you to edit it. The only
required edit is `collection_path`, your comics folder. Every other option is listed below it,
**commented out**, with its default and what it does:

```yaml
config_version: 2
collection_path: ~/comics

# match:
  # strong_threshold: 90      # title score (0-100): applied without asking
  # medium_threshold: 70
```

To change an option, remove its leading `# ` (and the `# ` of its section line, here `# match:`).
Only what is active counts: every other option comes from the packaged defaults and is merged
underneath, so options added in a new bdnex release work without touching your file.

**Keeping the file complete.** When a new version brings options your file does not list, bdnex
says so once per run (`note: 3 option(s) are not in your config file yet`). `bdnex config --update`
adds them, commented out, in the right section; no value you wrote is changed, an option you already
have (active or commented) is never repeated, and the previous file is kept as `bdnex.yaml.bak`. It also puts the
list of possible values back (`# convert | skip`) after any option of yours that has no comment, so you can
see what to type; a comment you wrote yourself is left alone.

**A commented-out option is not a setting.** `# pdf: convert` shows the default and changes nothing: PDFs are
still converted. To keep them as PDFs, remove the `# ` and write `pdf: tag` (the tags go into the PDF) or `pdf: skip` (the PDF is left as it is).

**From the web page.** With `bdnex serve --allow-changes` the **Settings** page shows every option with its possible values and
default, and saves a change into this same file (checked as a whole first, the previous file kept as `bdnex.yaml.bak`, your
comments kept). Folders (`collection_path`, `library`), secrets (`api.token`, `api.password`, the fetcher address) and lists
(`paths.rules`, `import.sources`) are edited in the file.

## Commands

| Command | Effect |
|---|---|
| `bdnex config` | print the effective configuration (defaults + yours) |
| `bdnex config --defaults` | print every option with its default |
| `bdnex config --update` | add the options your file does not list yet, commented out, and restore the list of possible values after options that lost it |
| `bdnex config --path` | print the config file path |
| `bdnex config --edit` | open the file in `$VISUAL` / `$EDITOR` |

## Validation and stale files

The merged configuration is validated on every start. A mistake is reported by key
name, all at once, and bdnex does not continue:

```text
~/.config/bdnex/bdnex.yaml: invalid bdnex config:
  - imprt: unknown option (typo?)
  - match.strong_threshold: Input should be less than or equal to 100
```

A file written by an older bdnex has no `config_version` (or a lower one). It is
reported as **stale**: keys that no longer exist are ignored with a warning, the
rest keeps working. Review it with `bdnex config --defaults`, then set
`config_version` to the current value to silence the warning.

## Main options

```yaml
collection_path: ~/comics                 # your collection (required); file paths are stored relative to it
library: ~/.local/share/bdnex/bdnex.sqlite

match:
  use_embedded: true                     # a file's own ComicInfo.xml (its album's web address, series and number) helps to match it
  strong_threshold: 90                   # applied without asking when import.auto is true
  medium_threshold: 70                   # between medium and strong: ask

import:
  mode: in_place                         # in_place | copy | move
  auto: false                            # false: you check every file; true: strong matches are unasked
  workers: 0                             # background matching processes; 0 = CPU cores - 1, 1 = none
  lookahead: 100                         # files matched ahead of the one you are reviewing
  outside: ask                           # files from outside `collection_path`: ask | move | copy | leave
  sources: []                            # folders new files come from, each copied or moved in: see below
  thumbnails: auto                       # a picture of the file's first page beside the match question: auto | kitty | iterm2 | never
  series: ask                            # several files in a folder: ask | always (assume a series) | never (match each file alone)
  resume: true                           # a file already linked to an album is not matched again (`--force` redoes it)

duplicates:
  keys: [series, volume]                 # what makes two albums the same edition: series | volume | title
  action: ask                            # ask | skip | keep_both | replace
  prefer: [cbz, cbr, pdf]                # the format suggested first when two files are the same book
  versions_dir: _versions                # spare versions of an album, inside `collection_path`

trash:
  dir: .bdnex-trash                      # replaced files wait here, inside `collection_path`

watch:
  enabled: false                         # `bdnex serve` looks at the import sources by itself and imports what is new, asking nothing
  interval: 15                           # minutes between two looks

cover:
  download: true                         # keep each album's cover as a file in `dir` (linked from the library)
  dir: ~/.local/share/bdnex/covers       # where the cover files are kept
  check: auto                            # compare each file's cover with its album's: auto (when OpenCV is installed) | true | false
  match_percentage: 40                   # similarity (0-100) from which a cover is trusted

api:
  host: 127.0.0.1                        # `bdnex serve` listens here; anything but this machine needs a password or a token
  port: 8765
  token: null                            # when set, requests must send `Authorization: Bearer <token>`
  username: bdnex                        # the login of the web page, with `password`
  session_hours: 168                     # how long a browser stays logged in (the page has a login form and a log out)
  behind_proxy: false                    # true behind your own reverse proxy: the client is the first `X-Forwarded-For` address
  password: null                         # when set, the web page asks for `username` and this password (HTTP Basic: use HTTPS beyond your LAN)
  allow_changes: false                   # true: the web page can run jobs and change the library (see the docs)

write:
  format_preferred: cbz                  # cbz | none: convert other formats to CBZ (per cbr / pdf below), or never convert
  cbr: convert                           # convert | skip
  pdf: convert                           # convert | tag | skip (tag: keep the PDF, write the tags into it; skip: leave it as it is)
  pdf_mode: auto                         # auto | extract | render (extract keeps the scanned images untouched)
  pdf_dpi: 200                           # resolution when PDF pages have to be rendered
  pdf_render_format: auto                # for pages that have to be drawn: auto | png | jpeg (leave on auto: it picks per page)
  pdf_jpeg_quality: 90                   # quality (1-100) of a drawn page saved as JPEG: higher = bigger and closer to the PDF
  delete_original: ask                   # after a CBR / PDF was converted: ask | yes | no
  use_trash: true                        # a deleted original goes to the trash folder (false: deleted for good)
  min_free_space: 200                    # MiB that must stay free on the disk (and for the database) after writing
  conflict: ask                          # a ComicInfo.xml that differs from the database: ask | file | database
  corrupted: ask                         # a damaged archive that can not be repaired: ask | leave | move
  size_tolerance: 20                     # % the new archive may differ in size from the original (0 = no check)
  size_change: ask                       # when it differs more than that: ask | accept | refuse
  workers: 0                             # files written at once by write / move (0: the cores less one, at most 4; 1: one by one)
  workers: 0                             # files written at once by write / move (0: the cores less one, at most 4; 1: one by one)

paths:
  oneshot: 'OneShot/$language/$title - $authors/$title - $authors'
  series: 'Series/$series/$series - T$volume:02 - $title'
  rules: []                              # other templates for some albums, first match wins (see below)
  missing_field: ask                     # an empty value in a template: ask | placeholder | skip
  placeholder: Unknown                   # what `placeholder` writes

bedetheque:
  delay: [3, 10]                         # seconds waited before each album / series page request
  sitemap_delay: [0.5, 1.5]              # seconds waited between sitemap files
  pages_dir: ~/.local/share/bdnex/pages  # where album pages saved from your browser are looked up
  parallel: null                         # album pages fetched at once; empty: one per fetcher_endpoint
```

## Other templates for some albums: `paths.rules`

`paths.series` and `paths.oneshot` are the defaults. To place some albums elsewhere (a collection, an author),
list rules. To match one of several values, use a regular expression. Each rule has a `match` (the same terms as [`bdnex ls`](queries.md), all must match) and a
`template`. The first rule that matches the album wins; an album that matches none uses the default.

```yaml
paths:
  rules:
    - match: ['series:/^(antares|bételgeuse|centaure)$/']   # a regex: one of these series
      template: 'Collections/Aire Libre/$series/$series - T$volume:02 - $title'
    - match: author:uderzo
      template: 'Uderzo/$series/$series - T$volume:02 - $title'
```

`match` may be one string. Anything the query language knows works: `publisher:`, `genre:`, `language:`, `year:`,
`kind:oneshot`, `any:`... Check the result with `bdnex move -n`.

## Metadata source

```yaml
source: bedetheque          # or an installed plugin, or "package.module:factory"
plugins: {}                 # free-form options of third-party sources, by name
```

See [Metadata source plugins](../dev/plugins.md).

## Page fetching (Bédéthèque)

bdnex needs the album pages of Bédéthèque, which sit behind an anti-bot protection. A **fetcher** is a function that
takes a URL and returns the page as bytes; `bedetheque.fetcher` names it as `package.module:function`. The default is
`bdnex.importer.bedetheque:flaresolverr_fetch`, which asks a [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)
server whose address you give:

```yaml
bedetheque:
  fetcher_endpoint: "http://localhost:8191/v1"   # your FlareSolverr
```

- **Several endpoints:** `fetcher_endpoint` may be a list (it was a single string before; a single string still works).
  They are used **round-robin**, and when one fails the next is tried; one that failed is left alone for a minute as long
  as another works. With more than one, album pages are also fetched **in parallel**, as many at a time as there are
  endpoints (or `bedetheque.parallel`, 1 to 16). Each request still waits `bedetheque.delay` first, so several endpoints
  mean the album pages come faster, not more politely: use that only with servers that can take it. The progress bar lists
  the titles being fetched at that moment (`Abaddon-Shadmi-Tome-1-Abaddon · Ada-Baldi-Ada · …  (3 of 6 fetched)`, cut to the
  width of your terminal); it advances page by page, in order, because each page is handled as soon as it is the next one. To see which endpoint
  served each page, run `bdnex import -v`: every page logs `… fetched through http://flare2:8191/v1 in 3.2s`, and a
  failing endpoint is reported (even without `-v`) as `… failed (…): trying the next endpoint`.

  ```yaml
  bedetheque:
    fetcher_endpoint:
      - "http://flare1:8191/v1"
      - "http://flare2:8191/v1"
    parallel: 2            # optional: defaults to the number of endpoints
  ```

- **No `fetcher_endpoint`:** the default fetcher has nothing to talk to, so album pages are read from the pages you
  saved from your browser into `bedetheque.pages_dir` (under any name ending with the album id, e.g. `245127.html`).
  `bdnex import` says so once, at the start.
- **Your own fetcher:** write a function `def fetch(url: str) -> bytes` and name it, with whatever it needs in
  `fetcher_endpoint` (free strings, kept as a list; a fetcher reads them with `load_config().settings.bedetheque.fetcher_endpoint`, a
  list even when you wrote one address):

  ```yaml
  bedetheque:
    fetcher: "my_package.my_module:fetch"
    fetcher_endpoint: "http://localhost:8191"
  ```

- **`fetcher: null`** always reads the saved pages.

The setting is **checked when the configuration is read**: a module that can not be imported, a name that is not in it
or that is not a function is reported by key (`bedetheque.fetcher: module 'x' has no function 'y'`, with a hint when the
`:` is in the wrong place), before anything runs. Covers are never fetched by it: they are plain image downloads.

Sitemap files wait `bedetheque.sitemap_delay` (default 0.5 to 1.5 s) between downloads; album pages wait
`bedetheque.delay` (3 to 10 s). How a fetcher obtains a page, and respecting the site's terms of use, is up to you.

## Import sources

`collection_path` is the one folder bdnex organises. `import.sources` lists the folders new comics arrive in; each has a
`path`, a `mode` and, optionally, a `name`:

```yaml
import:
  sources:
    - {path: ~/Downloads/comics, mode: move}
    - {path: /mnt/nas/inbox, mode: copy, name: nas}
```

- **`mode: copy`** (the default): the originals are never modified. **`mode: move`**: the files are moved in and
  the folders they empty are removed.
- **`name`** is how the library refers to the folder (`@nas/…`); it defaults to the folder's name, and two sources
  must not end up with the same one. Set it so renaming the folder does not change it.
- `bdnex import --sources` imports what is new in all of them; `bdnex import <folder>` inside one follows its mode.
  See [Importing](importing.md) for the details, and [Paths](maintaining.md) for `bdnex relocate`.

## How the configuration is checked

Every command (except `bdnex config` and `completion`, which cope with a broken file) reads and checks the whole
configuration before it does anything, so a mistake is reported by key, not halfway through a run:

- **Types and ranges:** `import.workers: -1`, `match.strong_threshold: 500` or `write.delete_original: maybe` are errors.
- **Unknown keys** (a typo such as `imprt:`) are errors; keys an older bdnex used are warnings.
- **Path templates:** `paths.oneshot`, `paths.series` and every `paths.rules[].template` may only use the variables
  bdnex knows (`$kind $series $title $volume $language $year $publisher $isbn $genre $authors $writer $penciller
  $colorist`). `$autor` is refused with the list of the right ones.
- **Rules:** every term of `paths.rules[].match` must be valid [query](queries.md) syntax (a regular expression that
  does not compile, a number that is not one).
- **The fetcher:** `bedetheque.fetcher` must import and be callable.
- **Sources:** the collection and the sources must be separate folders (a source can not be the collection, contain it or
  sit inside it, and sources can not be nested in each other). `mode` is `copy` or `move`, a `name` is letters, digits, `_` and `-`, and no two sources share a name or a folder.
- **Warnings** (the command goes on): a `collection_path` that is not an existing folder (a disk that is not mounted?), a
  `fetcher_endpoint` set with no fetcher, an out-of-date `config_version`.

`bdnex config --edit` checks the file again as soon as you leave the editor and tells you what is wrong, so you do not
find out at the next command. Values changed by the program after loading are checked too.

## `yes` and `no` in YAML

YAML reads an unquoted `yes` or `no` as true or false. For `write.delete_original: yes | no` bdnex accepts both
spellings, quoted or not. (Other options take words such as `ask`, `skip` or `always`, which are not affected.)
