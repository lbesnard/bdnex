"""Pluggable page fetchers: how a source gets its web pages (``fetch`` in the configuration).

A *fetcher* is any callable ``url -> bytes``. A source (``Bedetheque(library, fetch=...)``)
uses it for every page it needs, always through the library cache. Nothing here is specific to one source.

Provided here:

- :func:`build_fetcher`: the fetcher selected by the config,
- :func:`load_fetcher`: resolve a ``"package.module:function"`` string (for a config option),
- :func:`flaresolverr_fetch`: the default fetcher, through one or several FlareSolverr servers (``fetch.endpoint``),
- :class:`DirectoryFetcher`: serves pages saved from a browser into a folder.
"""

from __future__ import annotations

import importlib
import logging
import itertools
import os
import threading
import time
from typing import Callable, Optional
import requests

from bdnex.config import load_config


logger = logging.getLogger(__name__)

Fetcher = Callable[[str], bytes]



class FetcherError(ValueError):
    """The ``fetch.fetcher`` setting does not lead to a usable function."""


def load_fetcher(spec: str) -> Fetcher:
    """Import ``"package.module:function"`` and return the callable.

    Raises :class:`FetcherError` with a hint when the module or function can not be found.
    """
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise FetcherError(f"fetcher must look like 'package.module:function', got {spec!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as err:
        raise FetcherError(f"can not import the module {module_name!r} of fetcher {spec!r} ({err}).{_hint(module_name, attr)}") from err
    try:
        func = getattr(module, attr)
    except AttributeError:
        raise FetcherError(f"module {module_name!r} has no function {attr!r} (fetcher {spec!r})") from None
    if not callable(func):
        raise TypeError(f"{spec} is not callable")
    return func


def _hint(module_name: str, attr: str) -> str:
    """Suggest 'parent.module:function' when the function name was written into the module path."""
    parent = module_name.rpartition(".")[0]
    if parent:
        try:
            if callable(getattr(importlib.import_module(parent), attr, None)):
                return f" Did you mean '{parent}:{attr}'? The part before ':' is the module, the part after is the function."
        except ImportError:
            pass
    return " The part before ':' must be a module (a .py file), the part after it a function inside that module."


def build_fetcher(settings) -> Fetcher:
    """Build the page fetcher selected by the ``fetch`` config section.

    ``fetch.fetcher: "package.module:function"`` (your own) is imported and used as the ``url -> bytes`` callable (it can read
    ``fetch.endpoint`` itself through ``load_config``). Without it, FlareSolverr is used when ``fetch.endpoint`` is set, and
    else the pages saved from a browser into ``bedetheque.pages_dir``.
    """
    if uses_saved_pages(settings):
        return DirectoryFetcher(settings.pages_dir)
    return load_fetcher(settings.fetch.fetcher or DEFAULT_FETCHER)


DEFAULT_FETCHER = "bdnex.fetchers:flaresolverr_fetch"
OLD_DEFAULT_FETCHER = "bdnex.importer.bedetheque:flaresolverr_fetch"  # what older configurations say: the same function


def is_default(spec: Optional[str]) -> bool:
    return not spec or spec in (DEFAULT_FETCHER, OLD_DEFAULT_FETCHER)


def uses_saved_pages(settings) -> bool:
    """Are pages read from a folder of saved ones? Yes when the default fetcher (FlareSolverr) has no address to talk to."""
    section = settings.fetch
    return is_default(section.fetcher) and not section.endpoint


_turn = itertools.count()     # which endpoint starts the next request: the load is spread round-robin
_turn_lock = threading.Lock()
_down: dict[str, float] = {}  # endpoint -> when it last failed
DOWN_FOR = 60.0               # seconds an endpoint that failed is left alone while the others work


def _order(endpoints: list[str]) -> list[str]:
    """The endpoints in the order to try for one request: round-robin start, those that failed recently last."""
    with _turn_lock:
        start = next(_turn) % len(endpoints)
        rotated = endpoints[start:] + endpoints[:start]
        now = time.monotonic()
        healthy = [e for e in rotated if now - _down.get(e, -DOWN_FOR) >= DOWN_FOR]
        return healthy + [e for e in rotated if e not in healthy]


class PageUnavailable(OSError):
    """The site answered, and the page is not there (404, 410): asking another endpoint would get the same answer."""


def _flaresolverr_request(endpoint: str, url: str, timeout: int) -> bytes:
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": timeout * 1000  # FlareSolverr expects milliseconds
    }
    response = requests.post(endpoint, json=payload, timeout=timeout + 10)
    response.raise_for_status()
    data = response.json()
    if data.get("status") == "ok":
        # FlareSolverr returns the page HTML inside solution.response, and the HTTP status the site answered in solution.status
        solution = data.get("solution")
        if not isinstance(solution, dict) or not isinstance(solution.get("response"), str):
            raise ValueError(f"FlareSolverr answered without a page for {url}")  # a malformed answer: the next endpoint is tried
        status = solution.get("status")
        if isinstance(status, int) and status in (404, 410):
            raise PageUnavailable(f"{url}: the site says the page is not there (HTTP {status})")
        if isinstance(status, int) and status >= 400:  # 429, 5xx...: not a page, whatever FlareSolverr's own status says
            raise RuntimeError(f"the site answered {url} with HTTP {status}, not a page")
        return solution["response"].encode("utf-8")
    raise RuntimeError(f"FlareSolverr failed to fetch {url}: {data.get('message')}")


def flaresolverr_fetch(url: str, timeout: int = 60) -> bytes:
    """Fetch a webpage using FlareSolverr.

    ``fetch.endpoint`` may list several FlareSolverr servers: requests are spread over them round-robin,
    and when one fails the next is tried (a server that failed is left for a minute, as long as another works).
    """
    settings = load_config().settings  # creates the file on first run, validates, merges defaults
    endpoints = settings.fetch.endpoint
    endpoints = [endpoints] if isinstance(endpoints, str) else list(endpoints or [])
    if not endpoints:
        raise FetcherError(
            "fetch.endpoint is not set in your config "
            "(the address of your FlareSolverr, e.g. http://localhost:8191/v1): `bdnex config --edit`")
    problems = []
    for endpoint in _order(list(endpoints)):
        started = time.monotonic()
        try:
            content = _flaresolverr_request(endpoint, url, timeout)
        except PageUnavailable:
            raise  # the page does not exist: no other endpoint would find it, and this one is working (it is not marked down)
        except (requests.RequestException, RuntimeError, ValueError, KeyError, AttributeError, TypeError) as err:
            _down[endpoint] = time.monotonic()
            problems.append(f"{endpoint}: {err}")
            logger.warning(f"{endpoint} failed ({err}): trying the next endpoint")  # a fallback is never silent
            continue
        _down.pop(endpoint, None)
        logger.info(f"{url.rsplit('/', 1)[-1]} fetched through {endpoint} in {time.monotonic() - started:.1f}s")  # -v
        return content
    raise RuntimeError(f"no FlareSolverr endpoint could fetch {url}: " + "; ".join(problems))


class DirectoryFetcher:
    """Serve album pages that were saved from a browser into ``directory``.

    A page is found by its album id: ``<id>.html`` or any ``*-<id>.html`` file.
    """

    def __init__(self, directory):
        self.directory = os.path.expanduser(str(directory))

    def __call__(self, url: str) -> bytes:
        from bdnex.importer.bedetheque.sitemap import source_id_from_url  # (saved pages are named by Bédéthèque's album id)

        source_id = source_id_from_url(url)
        if source_id is not None and os.path.isdir(self.directory):
            for name in sorted(os.listdir(self.directory)):
                if name == f"{source_id}.html" or name.endswith(f"-{source_id}.html"):
                    with open(os.path.join(self.directory, name), "rb") as fh:
                        return fh.read()
        raise FileNotFoundError(
            f"No saved page for {url}: save it in {self.directory} as '{source_id}.html'")
