"""Bédéthèque metadata source plugin."""

from bdnex.importer.bedetheque.bedetheque import image_fetch, Bedetheque, BlockedError, http_fetch
from bdnex.importer.bedetheque.fetchers import (uses_saved_pages, DirectoryFetcher, FetcherError, build_fetcher, flaresolverr_fetch,
                                                load_fetcher)
from bdnex.importer.source import Candidate


def page_workers(settings) -> int:
    """Pages fetched at once: ``bedetheque.parallel``, else one per fetcher endpoint; 1 when pages are read from a folder of saved ones."""
    if uses_saved_pages(settings):
        return 1
    section = settings.bedetheque
    return section.parallel or max(1, len(section.fetcher_endpoint))


def create_source(library, settings):
    """Factory used by the plugin registry (``source: bedetheque``)."""
    section = settings.bedetheque
    source = Bedetheque(library, fetch=build_fetcher(settings), sitemap_fetch=http_fetch,
                        image_fetch=image_fetch,
                        delay=tuple(section.delay), sitemap_delay=tuple(section.sitemap_delay))
    source.use_embedded = settings.match.use_embedded
    source.min_margin = settings.match.min_margin
    from bdnex.config import MATCH_WEIGHTS

    source.weights = {name: getattr(settings.match, option) for option, name in MATCH_WEIGHTS.items()}
    if not uses_saved_pages(settings):  # several endpoints (or `parallel`) fetch several pages at once
        source.parallel = page_workers(settings)
    return source


__all__ = ["Bedetheque", "BlockedError", "Candidate", "DirectoryFetcher", "FetcherError", "build_fetcher", "create_source",
           "http_fetch", "load_fetcher", "flaresolverr_fetch", "page_workers"]
