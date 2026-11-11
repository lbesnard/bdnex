![BDneX](https://github.com/lbesnard/bdnex/actions/workflows/ci.yml/badge.svg)
[![codecov](https://codecov.io/gh/lbesnard/bdnex/branch/main/graph/badge.svg?token=V9WJWRCTK5)](https://codecov.io/gh/lbesnard/bdnex)

BDneX french comics tagger and library manager (POF at this stage)

### Motivation
Contrary to music tagging, there is no agreed standard vocabulary for comics
tagging in general. However the ComicRack standard is used by most library
managers such as [Komga](https://komga.org/)

A few teams are working on metadata for American comics, such as [comic tagger](https://github.com/comictagger/comictagger)
This tool retrieves data from the ComicVine REST API [Comic Vine](https://comicvine.gamespot.com).
However it is mostly for american comics, and only the most famous french ones
are represented.

BDneX comes here to hopefully fill the gap, with search capabilities of metadata,
which then can be added to **CBZ** and **CBR** file format. 

Why doing this?
On big libraries, it becomes easy then to find a book, based on its genre,
community score, author, colorist, penciller!

Read List can then be generated and more easily shared accross the community as
based on metadata and not an obscure filename.

### Features
- `bdnex import`: fuzzy-matches comic files (`.cbz`, `.cbr`, `.pdf`) against the Bédéthèque
  sitemaps and records them in a SQLite library (series, one-shots, authors, genres, checksums)
- duplicate handling, resume, interactive choices or `--force`
- `bdnex ls`: search the library (`series:nains author:uderzo year:2010..2015 ^genre:humour`)
- `bdnex write` / `bdnex move`: write `ComicInfo.xml`, convert CBR and PDF to CBZ losslessly
  (verified before anything is replaced), rename into a library tree from your own templates
- validated YAML configuration, with a message when it is stale

Inspired by the [beets](https://github.com/beetbox/beets) music manager.

Bédéthèque serves album pages only to browsers, so full metadata needs the pages to be saved
from a browser (or fetched by your own function). See the documentation, *Getting started*.

## Installation

Requires Python 3.10 to 3.13. [uv](https://docs.astral.sh/uv/) is the recommended installer.

### Stable release (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/lbesnard/bdnex/main/install.sh | sh
```

The script fetches the latest GitHub release wheel and installs it with `uv tool`
(falling back to `pipx`, then `pip`).

Or directly with uv, from a specific release on the [Releases page](https://github.com/lbesnard/bdnex/releases):

```bash
uv tool install https://github.com/lbesnard/bdnex/releases/download/vX.Y.Z/bdnex-X.Y.Z-py3-none-any.whl
```

### Latest from source

```bash
uv tool install git+https://github.com/lbesnard/bdnex.git
```

### Development

```bash
git clone https://github.com/lbesnard/bdnex.git
cd bdnex
uv sync --extra dev
uv run pytest
uv run bdnex --help
```

### Releasing

Run the **Release** workflow from the GitHub Actions tab and pick `patch`, `minor`
or `major`. It runs the tests, bumps the version in `pyproject.toml`, tags it, builds
the wheel and publishes a GitHub release.


## Documentation

Build locally with `uv sync --extra docs && uv run sphinx-build -b html docs docs/_build/html`, then open `docs/_build/html/index.html`.

## Examples

```bash
bdnex import ~/comics            # match and record
bdnex ls series:nains            # query
bdnex write                      # write ComicInfo.xml into matched files
bdnex move --copy                # copy into the library tree defined in the config
```

Full documentation: `docs/` (build with the command above).
