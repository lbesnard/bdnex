"""Bédéthèque metadata source plugin."""

from bdnex.importer.bedetheque.bedetheque import Bedetheque, BlockedError, http_fetch
from bdnex.importer.bedetheque.fetchers import (DirectoryFetcher, FetcherError, build_fetcher, flaresolverr_fetch,
                                                load_fetcher)
from bdnex.importer.source import Candidate


def create_source(library, settings):
    """Factory used by the plugin registry (``source: bedetheque``)."""
    section = settings.bedetheque
    return Bedetheque(library, fetch=build_fetcher(settings), sitemap_fetch=http_fetch,
                      delay=tuple(section.delay), sitemap_delay=tuple(section.sitemap_delay))


__all__ = ["Bedetheque", "BlockedError", "Candidate", "DirectoryFetcher", "FetcherError", "build_fetcher", "create_source",
           "http_fetch", "load_fetcher", "flaresolverr_fetch"]
