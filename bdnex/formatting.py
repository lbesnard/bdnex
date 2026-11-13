"""Sizes, durations and paths as the commands show them, in one place (used by the organizer and the UI)."""

from __future__ import annotations

import os
from typing import Optional


def human_size(size: int) -> str:
    """``1536`` -> ``1.5 KiB``."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"


def human_duration(seconds: float) -> str:
    """``45`` -> ``45 s``, ``600`` -> ``10 min``, ``7200`` -> ``2.0 h``."""
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


def short_path(path: str, root: Optional[str]) -> str:
    """The path below ``root`` when it is in it (the rest of a line is the same for every file)."""
    if root:
        root = os.path.expanduser(root).rstrip("/") + "/"
        if path.startswith(root):
            return path[len(root):]
    return path
