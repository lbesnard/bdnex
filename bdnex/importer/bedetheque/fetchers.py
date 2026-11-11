"""Pluggable page fetchers for the Bédéthèque plugin.

A *fetcher* is any callable ``url -> bytes``. ``Bedetheque(library, fetch=...)``
uses it for every page it needs, always through the library cache.

Provided here:

- :func:`load_fetcher`: resolve a ``"package.module:function"`` string (for a config option),
- :func:`placeholder_fetch`: a stub that does nothing useful and says so,
- :class:`DirectoryFetcher`: serves pages saved from a browser into a folder.
"""

from __future__ import annotations

import importlib
import os
from typing import Callable

from bdnex.importer.bedetheque.sitemap import source_id_from_url

Fetcher = Callable[[str], bytes]


def load_fetcher(spec: str) -> Fetcher:
    """Import ``"package.module:function"`` and return the callable."""
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"fetcher must look like 'package.module:function', got {spec!r}")
    func = getattr(importlib.import_module(module_name), attr)
    if not callable(func):
        raise TypeError(f"{spec} is not callable")
    return func


def placeholder_fetch(url: str) -> bytes:
    """Placeholder fetcher: always fails with a clear message.

    Replace it with your own ``url -> bytes`` callable (see :func:`load_fetcher`).
    """
    raise NotImplementedError(
        f"No page fetcher is configured, cannot get {url}. "
        "Set a fetcher, or save the page from your browser (see DirectoryFetcher).")


class DirectoryFetcher:
    """Serve album pages that were saved from a browser into ``directory``.

    A page is found by its album id: ``<id>.html`` or any ``*-<id>.html`` file.
    """

    def __init__(self, directory):
        self.directory = os.path.expanduser(str(directory))

    def __call__(self, url: str) -> bytes:
        source_id = source_id_from_url(url)
        if source_id is not None and os.path.isdir(self.directory):
            for name in sorted(os.listdir(self.directory)):
                if name == f"{source_id}.html" or name.endswith(f"-{source_id}.html"):
                    with open(os.path.join(self.directory, name), "rb") as fh:
                        return fh.read()
        raise FileNotFoundError(
            f"No saved page for {url}: save it in {self.directory} as '{source_id}.html'")
