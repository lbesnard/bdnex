"""Bédéthèque metadata source plugin."""

from bdnex.importer.bedetheque.bedetheque import image_fetch, Bedetheque, BlockedError, http_fetch
from bdnex.importer.bedetheque.fetchers import (uses_saved_pages, DirectoryFetcher, FetcherError, build_fetcher, flaresolverr_fetch,
                                                load_fetcher)
from bdnex.importer.source import Candidate


def create_source(library, settings):
    """Factory used by the plugin registry (``source: bedetheque``)."""
    section = settings.bedetheque
    source = Bedetheque(library, fetch=build_fetcher(settings), sitemap_fetch=http_fetch,
                        image_fetch=image_fetch,
                        delay=tuple(section.delay), sitemap_delay=tuple(section.sitemap_delay))
    source.use_embedded = settings.match.use_embedded
    if not uses_saved_pages(settings):  # several endpoints (or `parallel`) fetch several pages at once
        source.parallel = section.parallel or max(1, len(section.fetcher_endpoint))
    return source


__all__ = ["Bedetheque", "BlockedError", "Candidate", "DirectoryFetcher", "FetcherError", "build_fetcher", "create_source",
           "http_fetch", "load_fetcher", "flaresolverr_fetch"]
