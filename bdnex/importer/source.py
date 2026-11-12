"""The contract between the importer and a metadata source plugin.

A plugin turns one website or database into :class:`~bdnex.library.AlbumInfo` objects. The importer
asks it for candidates, scores them, fetches the chosen one and stores the result with
``Library.add_album``; the plugin never writes to the database itself, except for the response cache
(``Library.cache_put`` / ``cache_get`` / ``cache_clear``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from bdnex.library.models import AlbumInfo


@dataclass(frozen=True)
class Candidate:
    """One possible album for a file name, found without any network access."""

    source_id: str  # id of the album at the source; (plugin name, source_id) identifies an album
    url: str  # what ``album_info`` is called with
    name: str  # normalised text the file name was compared with
    volume: Optional[int] = None  # None for one-shots and specials

    @property
    def kind(self) -> str:
        return "series" if self.volume is not None else "oneshot"


@runtime_checkable
class MetadataSource(Protocol):
    name: str
    """Short unique name, stored as ``AlbumInfo.source``."""

    fetch_errors: tuple[type[Exception], ...]
    """Exceptions meaning "this page can not be fetched now". The importer then falls back to
    :meth:`partial_info` instead of failing the file."""

    def is_ready(self) -> bool:
        """True when local data needed for :meth:`candidates` is available (e.g. a downloaded index)."""

    def prepare(self, recache: bool = False, progress: Optional[Callable[[str], None]] = None) -> None:
        """Download or refresh that local data. Called when not ready, or with ``--recache``."""

    def candidates(self, query: str, limit: int = 5) -> list[tuple[Candidate, float]]:
        """Best matches for a file name as (candidate, score 0-100). Must not use the network."""

    def album_info(self, url: str, recache: bool = False) -> AlbumInfo:
        """Full metadata of a candidate. May use the network (through the cache); raise one of
        ``fetch_errors`` when the page is unavailable."""

    def partial_info(self, candidate: Candidate) -> AlbumInfo:
        """What can be known from the candidate alone, with ``extra={"partial": True}``."""


# Optional: ``cover_image(url, recache=False) -> bytes | None`` returns an album's cover (``cover.check``).
# Optional: ``catalogue() -> list[Candidate]`` is every album known locally, so the user can search it by hand.
# Optional: ``warm() -> None`` loads the local matching data before the importer forks its worker
# processes (they then share it instead of loading it each).
REQUIRED = ("name", "fetch_errors", "is_ready", "prepare", "candidates", "album_info", "partial_info")


def check_source(source: object, spec: str = "") -> None:
    """Raise ``TypeError`` naming every member a plugin forgot."""
    missing = [member for member in REQUIRED if not hasattr(source, member)]
    if missing:
        raise TypeError(f"source {spec or type(source).__name__} is missing: {', '.join(missing)}")
