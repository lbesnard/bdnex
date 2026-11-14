"""``bdnex serve``: a read-only HTTP API and a small web page over the library."""

from __future__ import annotations

import argparse
import os
import sys

from bdnex.config import LOCAL_HOSTS
from bdnex.ui import context
from bdnex.ui.output import add_verbosity, setup_logging


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex serve",
        description="Serve the library over HTTP: a JSON API (documented at /docs) and a web page at /, "
                    "read-only unless you allow changes. Needs `pip install 'bdnex[api]'`.")
    parser.add_argument("--host", help="address to listen on (default api.host: 127.0.0.1, this machine only)")
    parser.add_argument("--port", type=int, help="port (default api.port: 8765)")
    parser.add_argument("--token", help="require `Authorization: Bearer <token>` (default api.token)")
    parser.add_argument("--password-env", default="BDNEX_PASSWORD", metavar="NAME",
                        help="the environment variable that holds the web page's password (default BDNEX_PASSWORD; "
                             "else api.password): the page then asks for api.username (or $BDNEX_USER) and it")
    parser.add_argument("--allow-changes", action="store_true",
                        help="let the web page run jobs and change the library (default api.allow_changes: false, read-only)")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    settings = context.settings(library=False)
    if settings is None:
        return 1
    try:
        import uvicorn

        from bdnex.api.app import create_app
    except ImportError:
        print("`bdnex serve` needs the API extra: pip install 'bdnex[api]'\n"
              "From a clone: uv run --extra api bdnex serve   (or once: uv sync --extra api)", file=sys.stderr)
        return 1
    if not context.has_library(settings):
        return 1
    host, port = args.host or settings.api.host, args.port or settings.api.port
    token = args.token or settings.api.token
    password = os.environ.get(args.password_env) or settings.api.password
    username = os.environ.get("BDNEX_USER") or settings.api.username
    if host not in LOCAL_HOSTS and not (token or password):
        print(f"Listening on {host} would show your library to the network: set api.password (or $BDNEX_PASSWORD) "
              "or api.token in the configuration, or listen on 127.0.0.1.", file=sys.stderr)
        return 2
    allow = args.allow_changes or settings.api.allow_changes
    print(f"bdnex serving the library on http://{host}:{port}/  (API documentation: /docs)  "
          + ("[changes allowed]" if allow else "[read-only: add --allow-changes (or api.allow_changes: true) to run tasks and edit]") + ("  [token required]" if token else "")
          + ("  [login required]" if password else ""), file=sys.stderr)
    uvicorn.run(create_app(settings, token=token, allow_changes=allow, password=password, username=username), host=host, port=port, log_level="warning")
    return 0
