# Web page and HTTP API

`bdnex serve` opens your library in a browser, and offers the same data as a JSON API for scripts (Komga helpers,
dashboards, shell one-liners). By default it is **read-only**: you can look at everything, and nothing can be changed.
For people who prefer a page to a terminal it can also **run tasks** (the same operations as the command line) once you
allow it.

```bash
pip install 'bdnex[api]'      # the extra: fastapi and uvicorn
# from a clone: uv run --extra api bdnex serve   (or once: uv sync --extra api)
bdnex serve                   # http://127.0.0.1:8765/, read-only
bdnex serve --allow-changes   # the page may also run tasks and answer reviews
```

## The page

| View | What it shows |
|---|---|
| **Overview** | counts, files by format, albums by decade, what needs attention (to review, damaged files, albums to fetch), your sources |
| **Albums** | a wall of covers; search with the [`bdnex ls` terms](queries.md); click a cover for the album, its credits and its files |
| **Series** | each series with a progress bar; open one to see every volume: filled when you have it, dashed when it is missing (it opens the album's page) |
| **Review** | the matches whose cover looked different: the file's cover next to the album's, with *Keep the match* and *Unlink* |
| **Health** | the report of [`bdnex check`](maintaining.md), and the duplicates |
| **Tasks** | previews and runs of the command-line operations, with their output as they run |
| **Files**, **Trash**, **Settings** | the files (searchable), the trash, and your configuration as the page sees it |

Links can carry a filter: `#/albums?q=series:nains`, `#/series?name=nains`, `#/files?q=format:pdf`.

## Tasks, and what "allow changes" means

Everything that changes your library or your files is a **task**: `check --fix`, download the album list (`init`), record
files as they are, **import** (match and read the pages of new files), `reparse`, write tags, move into the collection,
`convert` PDFs, repair, undo a move, relink, `relocate`, empty the trash (and restore a file from the Trash page), and `reset`. A task is
one of these commands run in the background, with the same code as the terminal. Each has a **Preview** (changes
nothing: `-n`) and **Run for real**. Only one task runs at a time; you can watch its output and cancel it.

**Questions on the page.** With "ask me my questions here" ticked (the default on the Tasks page), a real run asks
what the terminal would ask: which album is right, which side wins a conflict, what to do with a duplicate or a name
clash. The question appears at the top of the task, with the **same answers, keys and colours** as in the terminal
(`D`, `S`, `A`... press the key, or tap the answer; see [the keys](importing.md#the-keys-of-every-question)). The task
waits for you, can be cancelled while it waits, and goes on when you answer. Untick it and the run never asks: the
database wins a tag conflict, a name clash gets a number, originals are kept, and an import applies `--auto=all`.
The question comes with what the terminal draws beside it: the **first page of the file** being matched, or the two covers of
a review. Each candidate album has a **page ↗** link to its page on the site, to check before choosing. The page tells you when
a question waits: the tab title changes to "❓ bdnex: waiting for you", a banner with an **Answer** button shows on every view
and, if you press **Notify me** (browsers only allow notifications on HTTPS or localhost), a notification appears when the tab
is in the background. The same questions can be answered from a phone. Two tasks can not run unasked: `reset` only starts when the page can ask
you to confirm, and "Empty the trash" asks "Delete them for good?" (untick the box and it goes ahead, after the page's own
"cannot be taken back" confirmation). `init` has no preview: it downloads the album list (about 70 MB). `bdnex edit` (the
metadata editor), `series`, `relocate` and `config` stay on the command line for now. The list of earlier runs is kept next to the library
(`<library>.jobs.json`, the last 30 runs, 300 lines each), so it survives a restart; a task that was running when the server
stopped is listed as "interrupted" (what it already did stays done: run it again to continue). With `watch.enabled` the server also runs an import of the sources by itself (see [Importing](importing.md#watching-the-sources-watchenabled)).
The import task can also be told to touch no file
(`--database-only`) or to leave files where they are (`--no-move`).

Changes are off until you turn them on, with `api.allow_changes: true` in the configuration or `--allow-changes` for one
run. When they are on:

- the **login** (password or token) is required for every change as soon as one is set, and `bdnex serve` refuses a network address without one;
- a change must carry an `X-Requested-With: bdnex` header and may not come from another site (the `Origin` must be the
  page's own address), so a web page you happen to visit cannot drive your local server;
- tasks are a fixed list with checked options: queries are plain terms (never flags), and folders must be inside your
  collection or your import sources;
- the page and the terminal can both be open. Looking is always safe, but run changes from one place at a time: only the
  page's own tasks are serialised, the command line takes no lock.

Task results are kept in memory: restarting the server forgets the list of earlier runs (the work stays done).

## Options

| option | default | meaning |
| --- | --- | --- |
| `--host` | `api.host` (`127.0.0.1`) | address to listen on |
| `--port` | `api.port` (`8765`) | port |
| `--token` | `api.token` | require `Authorization: Bearer <token>` |
| `--password-env` | `BDNEX_PASSWORD` | the environment variable that holds the page's password (else `api.password`); the user name is `$BDNEX_USER` or `api.username` |
| `--allow-changes` | `api.allow_changes` (`false`) | let the page run tasks and answer reviews |

The server listens on this machine only. To reach it from another one (a phone, a NAS), give an address such as
`--host 0.0.0.0` **and a password or a token**: without one bdnex refuses to start, because the API shows your file
paths. With a **password** (`api.password`, or `BDNEX_PASSWORD` in the environment, with the user name `api.username`) the
page shows a **login form** (and a *Log out* button), which works on a phone and with a password manager. A successful login
sets a session cookie (HttpOnly, SameSite=Strict, Secure over HTTPS) that lasts `api.session_hours` (a week by default) and
ends at once when you log out; restarting the server logs everybody out. Scripts send the same login as HTTP Basic
(`curl -u name:password`), or the **token** (`Authorization: Bearer ...`); any of them is accepted when several are set.
Five wrong guesses from one place lock it out for five minutes (`429`, with `Retry-After`). Behind a reverse proxy set
`api.behind_proxy: true` so that the place is the client the proxy forwards (`X-Forwarded-For`); otherwise every visitor
looks like the proxy and one bad guesser would lock everybody out. Do not set it when the server is reachable directly: a
visitor could then choose its own address. Every answer carries `X-Frame-Options: DENY`, `nosniff` and a content security
policy (the page loads nothing from elsewhere), and a request body over 1 MB is refused.
`/healthz` needs no login and shows nothing of the library, for a container's health check. There is no encryption
(HTTP Basic sends the password as it is): put a reverse proxy with HTTPS in front of it if it leaves your home network.
To run it in a container, see [Docker](docker.md).

## Endpoints

JSON. The reading endpoints are `GET`; the ones that change something are marked and need `api.allow_changes`. `q` takes the same terms as [`bdnex ls`](queries.md)
(`series:nains year:2010..2015 ^genre:humour`, quote values with spaces).

| endpoint | returns |
| --- | --- |
| `/api/albums?q=&limit=&offset=` | albums matching `q`, paged (`limit` up to 1000, 100 by default) |
| `/api/people?q=&limit=&offset=` | every credited name once (a name is one row of the library's `people` table), most albums first, with the number of albums by role |
| `/api/albums/{id}` | one album with its credits, genres and files |
| `POST /api/albums/{id}/edit` | **change (a preview is not):** edit an album's fields by hand: `{"preview": true, "fields": {"title": "…", "credits": {"Writer": ["A"]}}}`; answers `changes` (as `bdnex edit -n` prints them) and, when stored, the new album. A bad value is `400` with the reason |
| `POST /api/edit` | **change (a preview is not):** `{"q": "series:nains", "set": ["publisher=Soleil"], "preview": true}`, as `bdnex edit QUERY --set`; at most 2000 albums, never an empty query |
| `/api/albums/{id}/cover` | the album's saved cover image (404 when none was downloaded yet) |
| `/api/files?q=&limit=&offset=` | files matching `q` (the `--files` terms), with their absolute paths |
| `/api/series?name=&missing=true` | series, what you own, and the albums you lack when the series page is known |
| `/api/trash` | files waiting in the trash |
| `/api/stats` | counts of files, albums, series... |
| `/api/health` | version, schema, whether a token or a login is required |
| `/api/overview` | the counts and state the front page shows, and whether changes are allowed |
| `/api/series/{id}` | one series, volume by volume: owned or missing |
| `/api/files/{id}/cover` | the first page of a file, as a small picture |
| `/api/review` | the matches waiting for a look |
| `POST /api/review/{file_id}` | **change:** `{"action": "keep"}` or `{"action": "unlink"}` |
| `/api/duplicates`, `/api/check` | what `bdnex duplicates` and `bdnex check` report |
| `/api/config` | the settings that matter to the page (no token, no fetcher address) |
| `/api/jobs/available`, `/api/jobs`, `/api/jobs/{id}?after=` | the tasks you can start, the runs, and one run with its output from line `after` |
| `POST /api/jobs` | start a task: `{"command": "move", "preview": true, "query": ["series:nains"]}`; `"interactive": true` lets a real run ask its questions; `import` takes `steps`, `auto` and `files`. A preview needs no permission; `"preview": false` is a **change** |
| `GET /api/settings`, `POST /api/settings` | every option with its kind, default and help (no token, password or fetcher address); **change (a preview is not):** `{"changes": {"write.pdf": "tag"}, "preview": true}`. The whole file is validated before it is written, the previous one is kept as `.bak`, comments stay, and the running server follows at once (except `api.*`: restart) |
| `POST /api/files/{id}/spare`, `/unlink`, `/trash` | **change:** mark a file as a spare version (`{"spare": true}`), forget its album, or move it to the trash (restorable). Never for a file under a copy-only source |
| `/api/jobs/{id}/image` | the picture shown with the question the task is waiting on (404 when there is none); `GET /api/overview` has `waiting` (the task, its question) |
| `POST /api/jobs/{id}/answer` | **change:** answer the question a task is waiting on: `{"question": 3, "choice": 0}` (`choices` for a list, `text` for a typed value). The question, with its answers, is in `GET /api/jobs/{id}` as `question` |
| `POST /api/jobs/{id}/cancel` | **change:** ask a running task to stop |

A bad query answers `400` with the reason. Reading opens the database read-only, so looking cannot damage it and can
happen while an import runs. After upgrading bdnex, run any other command once so the library is migrated: the
server reports `503` instead of migrating it itself.

## Examples

```bash
curl 'http://127.0.0.1:8765/api/albums?q=series:nains'
curl -H 'Authorization: Bearer s3cret' 'http://nas:8765/api/series?missing=true'
```

A script that lists what is missing from each series:

```python
import requests

api = "http://127.0.0.1:8765/api"
headers = {"Authorization": "Bearer s3cret"}  # only when api.token is set
for series in requests.get(f"{api}/series", params={"missing": "true"}, headers=headers).json()["items"]:
    print(series["name"], f"{series['owned']}/{series['known']}")
    for album in series["missing"]:
        print("   ", album["title"], album["url"])
```

To line the library up with a Komga server, read `/api/files` for the paths and the album each belongs to
(`album_id`), then call Komga's own API for the books. The bdnex side is one request:

```bash
curl 'http://127.0.0.1:8765/api/files?q=series:nains&limit=1000'
```
