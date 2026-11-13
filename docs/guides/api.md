# HTTP API and web page

`bdnex serve` shows your library read-only over HTTP: a JSON API for scripts (Komga helpers, dashboards, shell
one-liners) and a small web page to browse it. Nothing can be changed through it: import, write and edit stay in
the command line, where the questions and the safety checks are.

```bash
pip install 'bdnex[api]'      # the extra: fastapi and uvicorn
bdnex serve                   # http://127.0.0.1:8765/
```

Open `http://127.0.0.1:8765/` for the web page (albums, files, series with what is missing, trash, counts) and
`/docs` for the interactive documentation of every endpoint.

## Options

| option | default | meaning |
| --- | --- | --- |
| `--host` | `api.host` (`127.0.0.1`) | address to listen on |
| `--port` | `api.port` (`8765`) | port |
| `--token` | `api.token` | require `Authorization: Bearer <token>` |

The server listens on this machine only. To reach it from another one (a phone, a NAS), give an address such as
`--host 0.0.0.0` **and a token**: without one bdnex refuses to start, because the API shows your file paths. The
token is checked on every request; the web page asks for it once per browser tab. There is no encryption: put a
reverse proxy with HTTPS in front of it if it leaves your home network.

## Endpoints

All of them are `GET`, JSON, and read-only. `q` takes the same terms as [`bdnex ls`](queries.md)
(`series:nains year:2010..2015 ^genre:humour`, quote values with spaces).

| endpoint | returns |
| --- | --- |
| `/api/albums?q=&limit=&offset=` | albums matching `q`, paged (`limit` up to 1000, 100 by default) |
| `/api/albums/{id}` | one album with its credits, genres and files |
| `/api/albums/{id}/cover` | the album's saved cover image (404 when none was downloaded yet) |
| `/api/files?q=&limit=&offset=` | files matching `q` (the `--files` terms), with their absolute paths |
| `/api/series?name=&missing=true` | series, what you own, and the albums you lack when the series page is known |
| `/api/trash` | files waiting in the trash |
| `/api/stats` | counts of files, albums, series... |
| `/api/health` | version, schema, whether a token is required |

A bad query answers `400` with the reason. The database is opened read-only, so the server cannot damage it and it
can run while an import does. After upgrading bdnex, run any other command once so the library is migrated: the
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
