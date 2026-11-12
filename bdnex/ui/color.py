"""Terminal colour, off when it would make things worse (pipes, NO_COLOR, dumb terminals)."""

from __future__ import annotations

import os
import sys

RESET = "\x1b[0m"
CODES = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "bold": "1",
         "dim": "2", "red_bold": "1;31", "green_bold": "1;32", "cyan_bold": "1;36"}


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


# ----------------------------------------------------------------------------- scores
# red (bad) -> orange -> yellow -> green (good), as (score, (r, g, b)); mid-tones that read on dark and light terminals
SCORE_ANCHORS = ((20, (215, 58, 73)), (40, (227, 98, 9)), (60, (219, 171, 9)), (80, (46, 160, 67)), (100, (26, 127, 55)))


def score_rgb(score: float) -> tuple[int, int, int]:
    """The colour of a match score: 20 and below red, 40 orange, 60 yellow, 80 and above green."""
    score = max(SCORE_ANCHORS[0][0], min(SCORE_ANCHORS[-1][0], score))
    for (low, a), (high, b) in zip(SCORE_ANCHORS, SCORE_ANCHORS[1:]):
        if score <= high:
            ratio = (score - low) / (high - low)
            return tuple(round(x + (y - x) * ratio) for x, y in zip(a, b))
    return SCORE_ANCHORS[-1][1]


def score_hex(score: float) -> str:
    return "#%02x%02x%02x" % score_rgb(score)


def _xterm256(rgb: tuple[int, int, int]) -> int:
    """The closest colour of the 6x6x6 cube of 256-colour terminals."""
    red, green, blue = (round(channel / 255 * 5) for channel in rgb)
    return 16 + 36 * red + 6 * green + blue


def paint_score(text: str, score: float, on: bool) -> str:
    """``text`` in the colour of ``score`` (true colour when the terminal says so, else 256 colours)."""
    if not (on and text):
        return text
    red, green, blue = score_rgb(score)
    if os.environ.get("COLORTERM") in ("truecolor", "24bit"):
        return f"\x1b[38;2;{red};{green};{blue}m{text}{RESET}"
    return f"\x1b[38;5;{_xterm256((red, green, blue))}m{text}{RESET}"


def rule(width: int | None = None) -> str:
    """A horizontal line between two questions, as wide as the terminal (at most 100 columns)."""
    import shutil

    columns = width or shutil.get_terminal_size((80, 20)).columns
    return "─" * max(20, min(100, columns))
