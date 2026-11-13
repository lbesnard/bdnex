"""What every command needs before it starts: its settings, and a library to work on.

One wording and one exit code for the same problem in every command (``1``: configuration or library missing,
``2``: a query that does not parse).
"""

from __future__ import annotations

import sys
from typing import Optional

from bdnex.config import ConfigError, Settings, load_config

NO_LIBRARY_HINT = "run `bdnex import <folder>` first."


def settings(library: bool = True, doing: str = "", collection: bool = False) -> Optional[Settings]:
    """The settings, or ``None`` after saying why not (the command then returns 1).

    ``library=True`` also needs the library file to exist; ``doing`` says what there is nothing of
    (``nothing to reset``) in place of the hint to import first; ``collection=True`` also needs ``collection_path``.
    """
    try:
        loaded = load_config().settings
        if collection:
            loaded.require_collection()
    except ConfigError as err:
        print(err, file=sys.stderr)
        return None
    if library and not has_library(loaded, doing):
        return None
    return loaded


def has_library(settings: Settings, doing: str = "") -> bool:
    """Whether the library file exists; when it does not, say so (for commands that already hold their settings)."""
    if settings.library_path.exists():
        return True
    print(f"No library yet ({settings.library_path}): {doing or NO_LIBRARY_HINT}", file=sys.stderr)
    return False


def bad_query(err: Exception) -> int:
    """Say a query does not parse; the exit code to return."""
    print(f"bad query: {err}", file=sys.stderr)
    return 2
