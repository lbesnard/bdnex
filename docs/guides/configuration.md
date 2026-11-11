# Configuration

The configuration file is `~/.config/bdnex/bdnex.yaml` (or
`$XDG_CONFIG_HOME/bdnex/bdnex.yaml`), created on first run from the default
shipped with bdnex.

```yaml
bdnex:
  config_path: ~/.config/bdnex
  share_path: ~/.local/share/bdnex      # sitemaps, cached pages, covers

directory: /path/to/comics/library     # root of the managed library
library: ~/.local/share/bdnex/bdnex.sqlite

cover:
  match_percentage: 40                 # minimum cover similarity to trust a match

paths:                                  # path templates (see Writing)
  oneshot: '%language/oneShots/%title (%author) [%year]/%title (%author) [%year]'
  series: '%language/series/%title (%author)/%title - %volume'
```

Options for the planned `import` and `write` commands are documented on their
own pages. The `library` entry is already used by the database layer.
