"""Find and create metadata sources.

A source is selected by the ``source`` option of the configuration. It can be:

* a built-in name (``bedetheque``),
* the name of an installed package's entry point in the ``bdnex.sources`` group,
* a dotted path ``package.module:factory``.

The factory is called as ``factory(library, settings)`` and returns a
:class:`~bdnex.importer.source.MetadataSource`. Free-form options for a plugin go in the
``plugins:`` section of the configuration (``settings.plugins["<name>"]``).
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points

from bdnex.importer.source import MetadataSource, check_source

ENTRY_POINT_GROUP = "bdnex.sources"
BUILTIN = {"bedetheque": "bdnex.importer.bedetheque:create_source"}


class UnknownSource(Exception):
    """The configured source can not be found."""


def available_sources() -> dict[str, str]:
    """name -> ``module:factory`` for built-in and installed sources (built-ins win a name clash)."""
    installed = {ep.name: ep.value for ep in entry_points(group=ENTRY_POINT_GROUP)}
    return {**installed, **BUILTIN}


def _load(spec: str):
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise UnknownSource(f"{spec!r} is not a 'package.module:factory' path")
    try:
        factory = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as err:
        raise UnknownSource(f"can not load source {spec!r}: {err}") from err
    if not callable(factory):
        raise UnknownSource(f"{spec!r} is not callable")
    return factory


def create_source(name: str, library, settings) -> MetadataSource:
    """Instantiate the source called ``name`` (or located by a dotted path)."""
    spec = name if ":" in name else available_sources().get(name)
    if spec is None:
        known = ", ".join(sorted(available_sources())) or "none"
        raise UnknownSource(f"unknown source {name!r} (available: {known}; or use 'package.module:factory')")
    source = _load(spec)(library, settings)
    check_source(source, spec)
    return source


class OfflineError(FileNotFoundError):
    """A page was needed that is not in the library's cache, while working offline."""


def offline_fetch(url: str) -> bytes:
    """A fetcher that never touches the network: use it to work only from the stored pages."""
    raise OfflineError(f"{url} is not stored in the library and the network is not used")
