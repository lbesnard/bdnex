"""A small picture of a file's first page beside the match question, on terminals that can show images.

Two protocols are spoken, the ones most image-capable terminals have: the **kitty** graphics protocol (kitty,
Ghostty) and the **iTerm2** inline images (iTerm2, WezTerm). Detection is conservative, because writing an
image sequence to a terminal that does not know it prints garbage; ``import.thumbnails`` or the environment
variable ``BDNEX_THUMBNAILS`` (``kitty``, ``iterm2`` or ``never``) overrides it, which is also the way over SSH, where
the terminal's own variables usually do not reach the remote shell.
"""

from __future__ import annotations

import base64
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

KITTY, ITERM2 = "kitty", "iterm2"
THUMB_COLUMNS = 14  # width of the picture, in terminal cells
MIN_TERMINAL_COLUMNS = 100  # narrower than this, the picture would sit on top of the list
PICTURE_WIDTH = 180  # pixels of the picture that is sent; the terminal scales it into its cells
CELL_ASPECT = 2.0  # a terminal cell is about twice as tall as it is wide
KITTY_CHUNK = 4096  # the kitty protocol takes the data in pieces of at most this many base64 characters
OFF = {"never", "off", "no", "false", "0", "none"}


def protocol(setting: str = "auto", env=None, stream=None) -> Optional[str]:
    """``kitty``, ``iterm2``, or ``None`` when no picture should be written."""
    env = os.environ if env is None else env
    stream = sys.stdout if stream is None else stream
    wanted = (env.get("BDNEX_THUMBNAILS") or setting or "auto").lower()
    if wanted in OFF or not getattr(stream, "isatty", lambda: False)() or env.get("TERM") == "dumb":
        return None
    if wanted in (KITTY, ITERM2):
        return wanted
    term = env.get("TERM", "")
    if env.get("TMUX") or env.get("STY") or term.startswith(("screen", "tmux")):
        return None  # a multiplexer swallows the sequences unless it is set up to pass them through
    if env.get("KITTY_WINDOW_ID") or term == "xterm-kitty" or env.get("TERM_PROGRAM") == "ghostty":
        return KITTY
    if env.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm") or env.get("LC_TERMINAL") == "iTerm2":
        return ITERM2
    return None


def small_png(image: bytes, width: int = PICTURE_WIDTH) -> Optional[tuple[bytes, int, int]]:
    """``(png, width, height)`` of the image scaled to ``width`` pixels; ``None`` if it can not be read."""
    import pymupdf

    try:
        pixmap = pymupdf.Pixmap(image)
        if pixmap.alpha:
            pixmap = pymupdf.Pixmap(pixmap, 0)
        if pixmap.colorspace is None or pixmap.colorspace.n != 3:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
        height = max(1, round(pixmap.height * width / pixmap.width))
        scaled = pymupdf.Pixmap(pixmap, width, height)
        return scaled.tobytes("png"), width, height
    except Exception as err:
        logger.debug(f"no thumbnail: {err}")
        return None


def cells(width: int, height: int, columns: int = THUMB_COLUMNS) -> tuple[int, int]:
    """``(columns, rows)`` the picture covers: the same shape as the image, cells being taller than wide."""
    return columns, max(1, round(columns * height / width / CELL_ASPECT))


def escape(png: bytes, kind: str, columns: int, rows: int) -> str:
    """The escape sequence that draws ``png`` at the cursor, over ``columns`` x ``rows`` cells."""
    data = base64.b64encode(png).decode("ascii")
    if kind == ITERM2:
        return f"\x1b]1337;File=inline=1;width={columns};height={rows};preserveAspectRatio=1:{data}\x07"
    # kitty: a=T transmit and display, f=100 PNG, C=1 leave the cursor alone, q=2 no reply (a reply would land in the
    # input of the prompt)
    chunks = [data[i:i + KITTY_CHUNK] for i in range(0, len(data), KITTY_CHUNK)] or [""]
    parts = []
    for number, chunk in enumerate(chunks):
        more = 1 if number < len(chunks) - 1 else 0
        control = f"a=T,f=100,t=d,c={columns},r={rows},C=1,q=2,m={more}" if number == 0 else f"m={more}"
        parts.append(f"\x1b_G{control};{chunk}\x1b\\")
    return "".join(parts)


def place(png: bytes, kind: str, columns: int, rows: int, terminal_columns: int) -> str:
    """Everything to write so that the picture sits on the far right, beside the lines that follow.

    Room is made first (the lines are printed, then the cursor goes back up), the cursor position is saved,
    the picture is drawn at the right edge, and the cursor comes back: the question then draws itself on
    the left, over the same rows.
    """
    column = terminal_columns - columns + 1
    return f"{chr(10) * rows}\x1b[{rows}A\x1b7\x1b[{column}G{escape(png, kind, columns, rows)}\x1b8"


def show(image: bytes, kind: str, stream=None, terminal_columns: Optional[int] = None) -> int:
    """Draw the picture on ``stream``. Returns the rows it takes (0 when nothing was drawn)."""
    stream = sys.stdout if stream is None else stream
    if terminal_columns is None:
        import shutil
        terminal_columns = shutil.get_terminal_size((80, 24)).columns
    if terminal_columns < MIN_TERMINAL_COLUMNS:
        return 0
    thumbnail = small_png(image)
    if thumbnail is None:
        return 0
    png, width, height = thumbnail
    columns, rows = cells(width, height)
    stream.write(place(png, kind, columns, rows, terminal_columns))
    stream.flush()
    return rows
