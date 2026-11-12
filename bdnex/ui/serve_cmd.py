"""``bdnex serve``: a read-only HTTP API and a small web page over the library."""

from __future__ import annotations

import argparse
import sys

from bdnex.config import LOCAL_HOSTS, ConfigError, load_config
from bdnex.ui.output import add_verbosity, setup_logging


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex serve",
        description="Serve the library read-only over HTTP: a JSON API (documented at /docs) and a small web "
                    "page at /. Needs `pip install 'bdnex[api]'`.")
    parser.add_argument("--host", help="address to listen on (default api.host: 127.0.0.1, this machine only)")
    parser.add_argument("--port", type=int, help="port (default api.port: 8765)")
    parser.add_argument("--token", help="require `Authorization: Bearer <token>` (default api.token)")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    try:
        settings = load_config().settings
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    try:
        import uvicorn

        from bdnex.api.app import create_app
    except ImportError:
        print("`bdnex serve` needs the API extra: pip install 'bdnex[api]'", file=sys.stderr)
        return 1
    if not settings.library_path.exists():
        print(f"No library yet ({settings.library_path}): run `bdnex import <folder>` first.", file=sys.stderr)
        return 1
    host, port = args.host or settings.api.host, args.port or settings.api.port
    token = args.token or settings.api.token
    if host not in LOCAL_HOSTS and not token:
        print(f"Listening on {host} would show your library to the network: set api.token in the configuration "
              "(or pass --token), or listen on 127.0.0.1.", file=sys.stderr)
        return 2
    print(f"bdnex serving the library read-only on http://{host}:{port}/  (API documentation: /docs)"
          + ("  [token required]" if token else ""), file=sys.stderr)
    uvicorn.run(create_app(settings, token=token), host=host, port=port, log_level="warning")
    return 0
