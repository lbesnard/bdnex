"""What an import session reports and raises: outcomes, results, the exceptions that steer it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from bdnex.importer.matching import Match
from bdnex.importer.source import Candidate

TAGGED, PARTIAL, SKIPPED, DUPLICATE, FAILED, PRETEND, ALREADY = (
    "tagged", "partial", "skipped", "duplicate", "failed", "pretend", "already")
# What a user reads for the outcomes that were named after the database (stored values do not change).
OUTCOME_LABELS = {TAGGED: "complete", PARTIAL: "matched"}
STEPS = ("match", "fetch")


class ImportAborted(RuntimeError):
    """The user chose to stop the import: what was decided so far stays, nothing else is started."""


class GoBack(RuntimeError):
    """The user wants to take the previous decision back and make it again."""


class SkipFolder(RuntimeError):
    """The user wants to skip this file and every other one still to come from its folder."""


class NeedsTerminal(RuntimeError):
    """A question has to be asked but there is nobody to ask: the import stops instead of failing file by file."""


class Prompter(Protocol):
    def choose_match(self, path: str, matches: list[Match]) -> Optional[Candidate]:
        """Pick a candidate (or None to skip). ``matches`` may be empty."""

    def duplicate_action(self, path: str, others: list[str], what: str) -> str:
        """Return ``skip``, ``keep_both`` or ``replace``."""


@dataclass
class Journal:
    """What matching one file recorded, to be able to take it back."""

    logged: bool = False  # an import-log row
    linked: bool = False  # the file was linked to an album
    new_file: bool = False  # ... and its row in the library did not exist before
    created_album: Optional[int] = None  # an album that was added for it
    previous_album: Optional[int] = None  # what the file was linked to before (a file imported again with --force or --album)
    previous_review: Optional[str] = None  # ... and its review flag
    log_floor: Optional[int] = None  # the newest import-log row of the file before this one: what is newer than it is taken back
    trashed: list[int] = field(default_factory=list)  # files it replaced (trash ids)


@dataclass
class ImportResult:
    path: str
    outcome: str
    score: Optional[float] = None
    album_id: Optional[int] = None
    note: str = ""
    journal: Optional[Journal] = field(default=None, repr=False, compare=False)


@dataclass
class ImportReport:
    results: list[ImportResult] = field(default_factory=list)
    aborted_at: Optional[str] = None  # the file being decided when the user aborted
    total: int = 0  # files the run had to look at (only set when it was aborted)

    def count(self, outcome: str) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)


def album_label(album: dict) -> str:
    """``Nains - T01 - Redwin de la Forge`` for messages."""
    title, series, volume = album.get("title") or "", album.get("series"), album.get("volume")
    if series and volume is not None:
        return f"{series} - T{int(volume):02d}" + (f" - {title}" if title and title != series else "")
    return title or series or "?"
