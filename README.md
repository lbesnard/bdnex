![BDneX](https://github.com/lbesnard/bdnex/actions/workflows/test.yml/badge.svg)
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

### Current Features
- retrieve sitemaps from bedetheque.com 
- levenhstein fuzzy string matching to find album name on external website
    (since no API is available)
- alternatively, there is currently a duckduckgo search, but will probably be
    deprecated
- Parse content of webpage with beautifulSoup
- convert parsed metadata into ComicInfo.xsd template
- Image comparaison between online cover and archive cover to bring confidence
    into creating metadata file

### Roadmap (?)
Further Feature(?):
- SQLight database to keep record of already processed data
- Interactive mode
- catalog manager
- renaming convention, based on user conf in ~/.local/bdnex/bdnex.ini
- add more "API", fmor bdfuge ...
- resume

Get inspiration from beets music manager: [beets](https://github.com/beetbox/beets)


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


## Examples:

```
bdnex -d /tmp/  # folder containing albums
bdnex -f /tmp/album.cbz  # single album
```

```commandline
2022-07-22 02:22:28,605 - INFO     - bdnex.ui - Processing /tmp/dummy.cbz
2022-07-22 02:22:28,605 - INFO     - bdnex.lib.bdgest - Searching for "dummuy"" in bedetheque.com sitemap files
2022-07-22 02:22:28,605 - DEBUG    - bdnex.lib.bdgest - Searching for "dummy"" in bedetheque.com sitemap files [FAST VERSION]
2022-07-22 02:22:28,605 - DEBUG    - bdnex.lib.bdgest - Merging sitemaps
2022-07-22 02:22:32,993 - DEBUG    - bdnex.lib.bdgest - Match album name succeeded
2022-07-22 02:22:32,993 - DEBUG    - bdnex.lib.bdgest - Levenhstein score: 53.333333333333336
2022-07-22 02:22:32,993 - DEBUG    - bdnex.lib.bdgest - Matched url: https://m.bedetheque.com/BD-dummy.html
2022-07-22 02:22:32,993 - DEBUG    - bdnex.lib.bdgest - Parsing JSON metadata from already parsed web page ~/.local/share/bdnex/bedetheque/albums_json/BD-dummy.json
2022-07-22 02:22:33,002 - INFO     - bdnex.lib.bdgest - Converting parsed metadata to ComicRack template
2022-07-22 02:22:33,011 - DEBUG    - bdnex.lib.cover - Cover ~/.local/share/bdnex/bedetheque/covers/Couv_dummy.jpg already downloaded
2022-07-22 02:22:33,011 - INFO     - bdnex.lib.cover - Checking Cover from input file with online cover
2022-07-22 02:22:33,442 - INFO     - bdnex.lib.cover - Cover matching percentage: 44.9264705882353
2022-07-22 02:22:33,442 - INFO     - bdnex.lib.comicrack - Add ComicInfo.xml to /tmp/dummy.cbz
2022-07-22 02:22:33,442 - INFO     - bdnex.lib.comicrack - Create ComicInfo.xml
2022-07-22 02:22:33,444 - INFO     - bdnex.lib.comicrack - Successfully appended ComicInfo.xml to /tmp/dummy.cbz
2022-07-22 02:22:33,445 - INFO     - bdnex.ui - Processing album done
...
```
