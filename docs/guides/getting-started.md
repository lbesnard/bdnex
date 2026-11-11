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

On first use bdnex creates `~/.config/bdnex/bdnex.yaml` and downloads the
Bédéthèque sitemaps (47 files) into `~/.local/share/bdnex`. This only happens
once.

## Tag your albums

Tag one file:

```bash
bdnex -f "Nains 1 - Redwin de la forge.cbz"
```

or a whole folder, recursively (`.cbz` and `.cbr`):

```bash
bdnex -d /path/to/comics
```

For each file bdnex:

1. finds the most similar album name in the Bédéthèque sitemaps;
2. downloads the album page (cached) and its cover;
3. compares the online cover with the first page of your file;
4. if they look alike, adds a `ComicInfo.xml`; otherwise it asks you, and lets
   you search the album manually.

```{note}
`.cbr` files are converted to `.cbz` when tags are added, because the RAR
format cannot be written without proprietary tools.
```

## Next steps

- [Import into the library](importing.md)
- [Query your library](queries.md)
- [Write tags and organise files](writing.md) *(planned)*
- [Configuration](configuration.md)
