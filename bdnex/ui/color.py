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


# ----------------------------------------------------------------------------- roles
# A command names what a piece of text IS; the colour is decided here, once, so the same thing looks the same in every
# command: what to look at is bold, what is wrong is red, what asks for a decision is yellow, what is fine is green,
# what you can type next is cyan, and what is only context (paths, explanations) is dim.
ROLES = {
    "title": "bold",        # a heading, the name of an album or series: where the eye lands first
    "name": "bold",
    "path": "dim",          # a file path: needed to find the file, but never the point
    "note": "dim",          # an explanation, a count of what was skipped, how a score was made
    "good": "green",        # confirmed, done, kept, complete
    "warn": "yellow",       # in doubt, waits for you, left as it is
    "bad": "red",           # contradicted, failed, wrong, unlinked
    "hint": "cyan",         # a command or option you can type, a number you can use
    "count": "cyan_bold",   # a total that sums up a run
}

VERDICTS = {  # the words commands use for a judgement, in the role they deserve
    "confirmed": "good", "kept": "good", "tagged": "good", "complete": "good",
    "unverified": "note", "unconfirmed": "warn", "doubtful": "warn", "skipped": "warn", "partial": "warn",
    "contradicted": "bad", "wrong": "bad", "failed": "bad", "missing": "bad",
}


def role(text: str, name: str, on: bool) -> str:
    return paint(text, ROLES[name], on)


def verdict(text: str, kind: str, on: bool) -> str:
    """``text`` coloured as the judgement ``kind`` (``confirmed`` green, ``doubtful`` yellow, ``contradicted`` red...)."""
    return role(text, VERDICTS.get(kind, "note"), on)


def hints(text: str, on: bool) -> str:
    """The ```commands``` in a sentence in the colour of a hint: the next thing to type stands out of the prose."""
    import re

    return re.sub(r"`([^`\n]+)`", lambda found: paint(found.group(0), ROLES["hint"], on), text) if on else text


class LogFormatter:
    """``logging`` lines with the level coloured (and the rest of the line quiet) when stderr is a terminal."""

    LEVELS = {"DEBUG": "dim", "INFO": "cyan", "WARNING": "yellow", "ERROR": "red_bold", "CRITICAL": "red_bold"}

    def __init__(self, fmt: str, on: bool):
        import logging

        self.inner = logging.Formatter(fmt)
        self.on = on

    def format(self, record) -> str:
        if not self.on:
            return self.inner.format(record)
        import copy

        shown = copy.copy(record)
        style = self.LEVELS.get(record.levelname, "dim")
        shown.levelname = paint(f"{record.levelname:<7}", style, True)
        shown.name = paint(record.name, "dim", True)
        shown.threadName = paint(record.threadName, "dim", True)
        if record.levelno >= 30:
            shown.msg, shown.args = paint(record.getMessage(), "yellow" if record.levelno < 40 else "red", True), None
        elif record.levelno <= 10:
            shown.msg, shown.args = paint(record.getMessage(), "dim", True), None
        return self.inner.format(shown)

    def __getattr__(self, name):  # a logging.Handler asks its formatter for a few more things
        return getattr(self.inner, name)
