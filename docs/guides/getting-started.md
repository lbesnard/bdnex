# Getting started

## Install

The recommended way is [uv](https://docs.astral.sh/uv/):

```bash
curl -fsSL https://raw.githubusercontent.com/lbesnard/bdnex/main/install.sh | sh
```

This installs the latest release wheel as an isolated tool. `pipx` and `pip`
work too. Python 3.10 to 3.13 is required.

From a clone, for development:

```bash
git clone https://github.com/lbesnard/bdnex.git
cd bdnex
uv sync --extra dev
uv run bdnex --help
```

## First run

The first command creates `~/.config/bdnex/bdnex.yaml` and tells you to edit it. Set `directory`
to your comics folder (`bdnex config --edit`); see [Configuration](configuration.md).

`.cbr` files need an `unrar` (or `7z` / `bsdtar`) program on your system to be read.

## A first session

```bash
bdnex import ~/comics        # match every .cbz / .cbr / .pdf and record it in the library
bdnex ls                     # what is in the library
bdnex ls --files tagged:false   # files that found no album
bdnex write                  # write ComicInfo.xml into the matched files
bdnex move --copy            # or: copy them, renamed, into your library tree
```

`bdnex import -W ~/comics` imports and writes in one go. Add `-n` to any command to see what
it would do first.

## Album pages

Matching works offline from the Bédéthèque sitemaps, which bdnex downloads itself. The details of
an album (authors, publisher, ISBN, summary, genres) come from its page, and **bedetheque.com
blocks scripted page requests**. bdnex does not try to get around that. Without the page, an
album is still imported from its sitemap URL (series, volume, title, series or one-shot) and shown
as `partial`: `bdnex ls partial:true` lists them.

To complete them, save the album page from your browser (Ctrl+S, "Web page, HTML only") into
`~/.local/share/bdnex/pages/` (`bedetheque.pages_dir`) under any name that ends with the album id
(`BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html`, or just `245127.html`), then run
`bdnex import --retry-partial` to complete the partial albums (nothing else is redone). Pages are cached in the library: each is read once.

You can also supply pages another way with your own function; see *Page fetching* in
[Configuration](configuration.md).

## Next steps

- [Import into the library](importing.md)
- [Query your library](queries.md)
- [Write tags and organise files](writing.md)
- [Configuration](configuration.md)
