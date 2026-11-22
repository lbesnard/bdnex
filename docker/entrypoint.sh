#!/bin/sh
# First start: give the container a configuration and an empty library, so `bdnex serve` has something to open.
set -e
# The three folders must exist and be writable by the user the container runs as (PUID:PGID). A missing one is created by
# Docker as root, and then nothing can be written: say so, rather than a traceback from deep inside bdnex.
for folder in /config /data /covers; do
    if [ ! -w "$folder" ]; then
        echo "bdnex: $folder is not writable by $(id -u):$(id -g). Create the folder on the host first (mkdir -p config data covers)," >&2
        echo "bdnex: make it yours (chown $(id -u):$(id -g) ...), or set PUID / PGID in .env to its owner (id -u, id -g)." >&2
        exit 1
    fi
done
if [ ! -f "$BDNEX_CONFIG" ]; then
    cp /usr/share/bdnex-docker.yaml "$BDNEX_CONFIG"
    chmod 600 "$BDNEX_CONFIG"  # you will put passwords and tokens in it: nobody else on the host should read it
    echo "bdnex: wrote $BDNEX_CONFIG (edit it to change the settings)" >&2
fi
python - <<'PY'
from bdnex.config import load_config
from bdnex.library import Library

settings = load_config().settings
if not settings.library_path.exists():
    Library.for_settings(settings).close()  # creates the empty library
PY
exec "$@"
