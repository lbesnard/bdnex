"""Bédéthèque metadata source plugin."""

from bdnex.importer.bedetheque.bedetheque import Bedetheque
from bdnex.importer.bedetheque.fetchers import DirectoryFetcher, load_fetcher, placeholder_fetch
from bdnex.importer.bedetheque.sitemap import Candidate

__all__ = ["Bedetheque", "Candidate", "DirectoryFetcher", "load_fetcher", "placeholder_fetch"]
