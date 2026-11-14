# Run bdnex in Docker

The image runs `bdnex serve`: the web page and the API, with a login. It is meant for one person (or a family) on a
home network, or behind a reverse proxy such as Traefik. Every command of the command line is also in the
container: `docker compose exec bdnex bdnex import ...`.

## Start it

```bash
cp .env.example .env          # then edit it: COMICS_DIR, BDNEX_PASSWORD, PUID / PGID (`id -u`, `id -g`)
docker compose up -d --build
```

Open `http://localhost:8765/`: the browser asks for the user name (`BDNEX_USER`, `bdnex` by default) and the
password (`BDNEX_PASSWORD`). The compose file refuses to start without a password, because the page can change your
files. The first start writes a configuration and an empty library.

| Volume | In the container | What it holds |
|---|---|---|
| `COMICS_DIR` | `/comics` | your comics: the collection (`collection_path`) |
| `CONFIG_DIR` (`./config`) | `/config` | `bdnex.yaml`, your configuration |
| `DATA_DIR` (`./data`) | `/data` | the library (SQLite) |
| `COVERS_DIR` (`./covers`) | `/covers` | the downloaded covers: map it wherever you like, even to another disk |

Keep `/data` and `/config` in volumes: they are your library. The container runs as `PUID:PGID`, so the files it
writes in your comics folder belong to you, not to root.

## The first time

The album list (what file names are matched against) is downloaded by the command line, once:

```bash
docker compose exec bdnex bdnex init
docker compose exec bdnex bdnex import --as-is /comics     # or any other command; see the guides
```

Edit `./config/bdnex.yaml` for the rest (`bdnex config --update` lists every option). New downloads: map their folder as
a volume (the commented `INBOX_DIR` line) and add it to `import.sources` as `/inbox`. Your page fetcher
(`bedetheque.fetcher_endpoint`) goes in that file too, never in the image, and it must be reachable from the container.
CBR files are read with `bsdtar`, which the image has. For the cover check, build with `--build-arg EXTRAS=api,cover`
(OpenCV, about 100 MB more).

## Behind Traefik, or any proxy

The login (a form and a session cookie) travels as it is without HTTPS, so use HTTPS as soon as the page leaves your LAN. Set
`api.behind_proxy: true` in `/config/bdnex.yaml` so that the limit on wrong guesses sees each client, not the proxy. Drop `ports:` and add the
labels shown in `compose.yaml` (router, entrypoint, TLS, port 8765). The proxy must pass `Host` and `Origin` unchanged: the
page refuses a change that comes from another site. You can also let the proxy do the login (Traefik's `basicauth`
middleware) and keep the page's own as a second lock.

## What the page can do

Browse, review, edit the metadata of an album (or of every album of a search), and run the tasks of the command line:
`init`, `import` (the page asks you which album is right, which side wins a conflict...), `reparse`, `write`, `move`,
`convert`, `repair`, `undo`, `relink`, the trash and `reset`. See [the web page and API](api.md) and, for editing, [Maintaining](maintaining.md#editing-an-album-or-a-series).
Still on the command line: `series`, `relocate` and `config` (edit `/config/bdnex.yaml` for settings).
