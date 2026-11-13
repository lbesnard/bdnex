# Getting started

## Install

The recommended way is [uv](https://docs.astral.sh/uv/):

```bash
curl -fsSL https://raw.githubusercontent.com/lbesnard/bdnex/main/install.sh | sh
```

This installs the latest release wheel as an isolated tool; run it again to upgrade. With uv alone, see *Installation* in
the README (`uv tool install --force` with the latest release address, or `uv tool install git+https://github.com/lbesnard/bdnex.git`
for the `main` branch, then `uv tool upgrade bdnex`). `pipx` and `pip`
work too. Python 3.10 to 3.13 is required.

From a clone, for development:

```bash
git clone https://github.com/lbesnard/bdnex.git
cd bdnex
uv sync --extra dev
uv run bdnex --help        # `uv run` uses the clone; a bdnex installed earlier may be older
```

`.cbr` files need an `unrar` (or `7z` / `bsdtar`) program on your system to be read.

The optional cover check needs OpenCV: `pip install 'bdnex[cover]'` (or `uv tool install 'bdnex[cover]'`).

## First run

The first command creates `~/.config/bdnex/bdnex.yaml` and tells you to edit it:

```bash
bdnex config --edit        # set `collection_path` to your comics folder
```

`bdnex config --path` prints where the file is, `bdnex config --defaults` lists every option.
See [Configuration](configuration.md).

## A first session

```bash
bdnex init                   # 1. download the album list, once (about a minute, 70 MB)
bdnex import -n ~/comics     # 2. preview: what would be matched (nothing is changed)
bdnex import ~/comics        # 3. import: you confirm each match (no folder: your `collection_path`)
bdnex ls                     # 4. what is in the library
bdnex ls --unmatched         #    ... and which files found no album
bdnex write -n               # 5. preview the tags that would be written
bdnex write                  #    write ComicInfo.xml into the matched files
```

**What import does.** It has two steps. *Match* compares every `.cbz` / `.cbr` / `.pdf` name with the
album list from `bdnex init`. *Fetch* reads each matched album's page. For each file you see the best
candidates, with the best one pre-selected:

- press enter to accept it, choose another candidate, or skip the file;
- the next files are matched in the background while you decide, so you never wait for matching;
- every decision is saved at once: Ctrl-C loses nothing, and running the same command again only
  looks at the files that are not done.

After your last decision bdnex fetches the album pages (authors, publisher, ISBN, summary, genres)
one by one, slowly on purpose, with a progress bar, and completes the albums. `--match` or `--fetch`
alone runs just one of the two steps.

Once you trust the matching, `bdnex import --auto ~/comics` applies the confident matches without
asking and only stops at the doubtful ones. `bdnex import -W ~/comics` imports and writes the tags
in one go. See [Importing](importing.md), and [How do I…?](cookbook.md) for the common tasks.

## Album pages

Matching works offline from the sitemaps. The details of an album come from its page, and
**bedetheque.com blocks scripted page requests**. bdnex does not try to get around that. Without
the page, an album is still imported from its sitemap URL (series, volume, title, series or
one-shot) and shown as *matched*, with `[not fetched]` in `bdnex ls`: `bdnex ls complete:false` lists them.

To complete them, either:

- save the album page from your browser (Ctrl+S, "Web page, HTML only") into
  `~/.local/share/bdnex/pages/` (`bedetheque.pages_dir`) under any name that ends with the album id
  (`BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html`, or just `245127.html`), then run
  `bdnex import --fetch`;
- or supply pages with your own function (`bedetheque.fetcher`); see *Page fetching* in
  [Configuration](configuration.md).

Pages are stored in the library: each is read once, and `bdnex reparse` can rebuild the albums
from them later, offline.

## Next steps

- [How do I…?](cookbook.md): short answers for the common tasks
- [Import into the library](importing.md)
- [Query your library](queries.md)
- [Write tags and organise files](writing.md)
- [Edit, find unmatched files, move your collection](maintaining.md)
- [Configuration](configuration.md)


## Tab completion

`bdnex completion bash|zsh|fish` prints a completion script for the commands, their options and file names.
Load it from your shell's startup file:

```bash
eval "$(bdnex completion zsh)"        # ~/.zshrc  (use bash in ~/.bashrc)
bdnex completion fish > ~/.config/fish/completions/bdnex.fish
```

The script is read from the program's own `--help`, so it follows the version you installed.
