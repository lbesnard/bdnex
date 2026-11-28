# Comic Vine

[Comic Vine](https://comicvine.gamespot.com/) is a third place to take an album from, for the comics Bédéthèque does not have (American,
English). Like [bdtheque.com](importing.md) it is **not a matching source**: bdnex has no list of its pages, so nothing searches it. You give the
address of a page, in the review screen, and the file is linked to what the page says.

| Site | Role | Keys of the review screen |
|---|---|---|
| **bedetheque.com** | the main source: an offline list of every album, so files are matched by name | `t` (candidates), `o` (search), `i` (an album address or number), `a` (a series address) |
| **bdtheque.com** | a second place, by address: a *series* page | `i` |
| **Comic Vine** | a third place, by address: an *issue* or a *volume* page | `i`, `a` |

## Set it up

1. Install the extra (it needs Python 3.11 or newer): `pip install "bdnex[comicvine]"`, or `uv sync --extra comicvine`. The Docker image has it.
2. Get a key at [comicvine.gamespot.com/api](https://comicvine.gamespot.com/api/) and give it to bdnex, in `bdnex config --edit`:

   ```yaml
   comicvine:
     api_key: "your key"
   ```

   or in the environment, which wins over the file: `BDNEX_COMICVINE_API_KEY`. It is a secret: `bdnex config` and the web page hide it, and a hook
   never gets the variable.

bdnex reads Comic Vine with [Simyan](https://github.com/Metron-Project/Simyan), which holds Comic Vine's limits (one request a second, 200 an
hour) and keeps its own cache. The cache and the limit counters are two small files next to the library (`comicvine-cache.sqlite`,
`comicvine-ratelimits.sqlite`; a volume in Docker). An answer is kept `comicvine.cache_days` (14) days: looking at a page again costs no request.
Comic Vine's terms are for non-commercial use.

## Link files

- **An issue** (`https://comicvine.gamespot.com/fell-1-feral-city/4000-123456/`): press `i`, paste it. The file under the cursor is linked to that
  album. Two requests (the issue, its volume).
- **A volume** (`https://comicvine.gamespot.com/fell/4050-18040/`): mark the files (`m` marks all of the screen), press `a` (or `i`), paste it. Each
  file gets the issue of the volume with the **number in its file name** (`T03`, `Tome 3`, `#3`). A question lists what will be linked before
  anything is. A file with no number, or a number the volume has no issue for, is left as it is.

A volume costs one request for the volume and **one per file** that matches. With the limit of 200 an hour, a long run can stop: the files read so
far are offered, and giving the address again later does the rest (what was read is in the cache, so it is free).

## What is stored

The album is stored with `source = comicvine` and the issue's id: its title (or `#3` when the issue has no name), the volume as series, its
number as `volume`, the cover date, the publisher (the volume's), the summary (text, not HTML), the credits (writer, penciller, inker, colorist,
letterer, cover artist, editor), and the characters, teams, locations and story arcs in `extra`. The language is `en`. A volume with one issue
is a one-shot. The `$series`, `$volume` and `$title` of your `paths` templates then take Comic Vine's English names. Quality, duplicates, versions,
`write` and `move` work on these files as on any other. The cover address is saved but the cover itself is not downloaded by the link.
