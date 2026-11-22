"""Moved to :mod:`bdnex.fetchers` (the page fetcher is not Bédéthèque's: any source uses it).

This name stays so that a configuration that says ``bdnex.importer.bedetheque:flaresolverr_fetch`` and old imports keep
working; it is the same module object, so nothing can drift apart.
"""

import sys

from bdnex import fetchers

sys.modules[__name__] = fetchers
