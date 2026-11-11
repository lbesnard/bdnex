"""Terminal colour, off when it would make things worse (pipes, NO_COLOR, dumb terminals)."""

from __future__ import annotations

import os
import sys

RESET = "\x1b[0m"
CODES = {"red": "31", "green": "32", "yellow": "33", "cyan": "36", "bold": "1", "dim": "2",
         "red_bold": "1;31", "green_bold": "1;32"}


def enabled(stream=None) -> bool:
    """Colour when writing to a terminal. ``NO_COLOR`` turns it off, ``FORCE_COLOR`` on."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    stream = stream if stream is not None else sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)()) and os.environ.get("TERM") != "dumb"


def paint(text: str, style: str, on: bool) -> str:
    return f"\x1b[{CODES[style]}m{text}{RESET}" if on and text else text
