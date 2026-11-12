# Configuration

## The file

bdnex reads `~/.config/bdnex/bdnex.yaml` (or `$XDG_CONFIG_HOME/bdnex/bdnex.yaml`,
or the file named by `$BDNEX_CONFIG`).

On the **first launch** bdnex creates a short, commented file there and tells you
to edit it. The only required edit is `directory`, your comics folder:

```yaml
config_version: 2
directory: ~/comics
```

Your file only lists what you change. Every other option comes from the packaged
defaults and is merged underneath, so options added in a new bdnex release work
without touching your file.

## Commands

| Command | Effect |
|---|---|
| `bdnex config` | print the effective configuration (defaults + yours) |
| `bdnex config --defaults` | print every option with its default |
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
directory: ~/comics                      # library root (required); file paths are stored relative to it
library: ~/.local/share/bdnex/bdnex.sqlite

match:
  strong_threshold: 90                   # applied without asking when import.auto is true
  medium_threshold: 70                   # between medium and strong: ask

import:
  mode: in_place                         # in_place | copy | move
  auto: false                            # false: you check every file; true: strong matches are unasked
  workers: 0                             # background matching processes; 0 = CPU cores - 1, 1 = none
  lookahead: 100                         # files matched ahead of the one you are reviewing
  outside: ask                           # files from outside `directory`: ask | move | copy | leave
duplicates:
  prefer: [cbz, cbr, pdf]                 # the format suggested first when two files are the same book
  versions_dir: _versions                # spare versions of an album, inside `directory`
  keys: [series, volume]
  action: ask                            # ask | skip | keep_both | replace

trash:
  dir: .bdnex-trash                      # replaced files wait here, inside `directory`

write:
  cbr: convert                           # convert | skip
  pdf: convert                           # convert | skip
  pdf_mode: auto                         # auto | extract | render
  conflict: ask                          # ask | file | database

paths:
  oneshot: 'OneShot/$language/$title - $authors/$title - $authors'
  series: 'Series/$series/$series - T$volume:02 - $title'
  missing_field: ask                     # ask | placeholder | skip
```

## Metadata source

```yaml
source: bedetheque          # or an installed plugin, or "package.module:factory"
plugins: {}                 # free-form options of third-party sources, by name
```

See [Metadata source plugins](../dev/plugins.md).

## Page fetching (Bédéthèque)

By default bdnex reads album pages you saved from your browser into
`bedetheque.pages_dir`. To supply pages another way, name a function that takes a
URL and returns the page as bytes:

```yaml
bedetheque:
  fetcher: "package.module:function"
  fetcher_endpoint: "http://localhost:8191"   # free string, only your function reads it
```

bedetheque's sitemap files wait `bedetheque.sitemap_delay` (default 0.5 to 1.5 s) between downloads;
album pages wait `bedetheque.delay` (3 to 10 s). bdnex does nothing with `fetcher_endpoint`. A fetcher can read it with
`load_config().settings.bedetheque.fetcher_endpoint`.
