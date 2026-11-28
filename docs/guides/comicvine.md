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

1. Install the extra: with uv the simplest is `all` (everything optional: this, and the web page), as the README says:
   `uv tool install "bdnex[all] @ git+https://github.com/lbesnard/bdnex.git"`, or `uv sync --extra all` from a clone. `bdnex[comicvine]` alone
   works too, with pip. The install script and the Docker image have it.
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

## Search by name

No address at hand? In the `o` window (or the `a` window), press **F4** (or the button **Search Comic Vine instead**): a box asks for the name of the
series. **Enter** searches (one request, not one per letter: the limit is 200 an hour), the arrows move, **Enter** picks, **Ctrl-O** opens the page
in your browser. The list says the year, the publisher and the number of issues of each volume. The volume you pick is used exactly like a pasted
volume address: every **marked** file gets the issue with the number in its name (with `o` and no mark: the file under the cursor). An album picked in `o` from Bédéthèque's list is still only for the file under the cursor: that is `a`'s job for a series. Answers are kept
(`comicvine.cache_days`), so the same search again costs nothing. Only volumes are searched, not issues.

## Link files

- **An issue** (`https://comicvine.gamespot.com/fell-1-feral-city/4000-123456/`): press `i`, paste it. The file under the cursor is linked to that
  album. Two requests (the issue, its volume).
- **A volume** (`https://comicvine.gamespot.com/fell/4050-18040/`): mark the files (`m` marks all of the screen), press `a` (or `i`), paste it. Each
  file gets the issue of the volume with the **number in its file name** (`T03`, `Tome 3`, `#3`). A table lists every file next to the issue it would get before
  anything is linked. A file with no number, or a number the volume has no issue for, is listed with no issue and is left as it is unless you pick one.

A volume is read in **two steps**, so that a "no" costs almost nothing:

1. **One request**: the volume, with the list of its issues. From that list bdnex works out which issue each file is, and the question shows the
   matching (`Fell T05.cbz -> Feral City (#5)`) and how many requests linking will cost.
   If the matching is wrong, **Enter** on a file lists the issues of the volume (already read: no request) and you pick the right one; **x** leaves a file out.
2. **After you press `y` (Link)**: one request per matched file (its credits, date, summary and cover). The files are linked when they are read.

With the limit of 200 an hour, a long series can stop in step 2: the files read so far are linked, and giving the volume again later does the rest
(what was read is in the cache, so it is free).

## What is stored

The album is stored with `source = comicvine` and the issue's id: its title (or `#3` when the issue has no name), the volume as series, its
number as `volume`, the cover date, the publisher (the volume's), the summary (text, not HTML), the credits (writer, penciller, inker, colorist,
letterer, cover artist, editor), and the characters, teams, locations and story arcs in `extra`. The language is `en`. A volume with one issue
is a one-shot. The `$series`, `$volume` and `$title` of your `paths` templates then take Comic Vine's English names. Quality, duplicates, versions,
`write` and `move` work on these files as on any other. The **cover** is downloaded right after the link, as a file in `cover.dir` (when `cover.download` is on), so that `c` can put it next to the file's own cover. That is a plain request to Comic Vine's picture, not the API, so it does not count against the 200 an hour. If the picture does not come, the link stays and there is just no cover.
