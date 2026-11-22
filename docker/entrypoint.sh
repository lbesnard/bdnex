#!/bin/sh
# First start: give the container a configuration and an empty library, so `bdnex serve` has something to open.
set -e
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
