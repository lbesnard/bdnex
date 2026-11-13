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
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

KITTY, ITERM2 = "kitty", "iterm2"
THUMB_COLUMNS = 24  # width of the picture, in terminal cells
MIN_TERMINAL_COLUMNS = 100  # narrower than this, the picture would sit on top of the list
PICTURE_WIDTH = 360  # pixels of the picture that is sent; the terminal scales it into its cells
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
    if env.get("STY"):
        return None  # GNU screen swallows the sequences
    if env.get("TMUX"):
        # tmux swallows them too, unless `allow-passthrough` is on; the terminal around it is then told apart by
        # the variables it exports (they survive inside tmux), the ones tmux itself sets are of no use
        if not passthrough(env):
            return None
        if env.get("KITTY_WINDOW_ID") or env.get("GHOSTTY_RESOURCES_DIR"):
            return KITTY
        return ITERM2 if env.get("LC_TERMINAL") == "iTerm2" or env.get("WEZTERM_EXECUTABLE") else None
    if term.startswith(("screen", "tmux")):
        return None
    if env.get("KITTY_WINDOW_ID") or term == "xterm-kitty" or env.get("TERM_PROGRAM") == "ghostty":
        return KITTY
    if env.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm") or env.get("LC_TERMINAL") == "iTerm2":
        return ITERM2
    return None


def in_tmux(env=None) -> bool:
    return bool((os.environ if env is None else env).get("TMUX"))


def passthrough(env=None) -> bool:
    """Is tmux set to hand escape sequences to the terminal (``set -g allow-passthrough on``)?"""
    import subprocess

    try:
        out = subprocess.run(["tmux", "show", "-gv", "allow-passthrough"], capture_output=True, text=True,
                             timeout=2, env=dict(os.environ if env is None else env)).stdout.strip()
    except Exception:
        return False
    return out in ("on", "all")


def through_tmux(sequence: str) -> str:
    """Wrap a sequence so that tmux passes it on to the terminal: ``ESC P tmux ;`` + doubled ESCs + ``ESC \\``."""
    return "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"


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


def place(png: bytes, kind: str, columns: int, rows: int, terminal_columns: int, tmux: bool = False) -> str:
    """Everything to write so that the picture sits on the far right, beside the lines that follow.

    Room is made first (the lines are printed, then the cursor goes back up), the cursor position is saved,
    the picture is drawn at the right edge, and the cursor comes back: the question then draws itself on
    the left, over the same rows.
    """
    column = terminal_columns - columns + 1
    picture = escape(png, kind, columns, rows)
    if tmux:
        picture = through_tmux(picture)  # the cursor moves stay tmux's own: only the picture goes through
    return f"{chr(10) * rows}\x1b[{rows}A\x1b7\x1b[{column}G{picture}\x1b8"


@dataclass
class Picture:
    """A picture ready to draw: the PNG, its protocol, its size in cells, and the terminal width it was placed for."""
    png: bytes
    kind: str
    columns: int
    rows: int
    terminal_columns: int


def prepare(image: bytes, kind: str, terminal_columns: int) -> Optional[Picture]:
    """A :class:`Picture` for this terminal width, or ``None`` when it is too narrow or the image can not be read."""
    if terminal_columns < MIN_TERMINAL_COLUMNS:
        return None
    thumbnail = small_png(image)
    if thumbnail is None:
        return None
    png, width, height = thumbnail
    columns, rows = cells(width, height)
    return Picture(png, kind, columns, rows, terminal_columns)


def pair(left: bytes, right: bytes, height: int = 360, gap: int = 16) -> Optional[bytes]:
    """Two images side by side in one PNG (same height), or ``None`` when one can not be read."""
    import pymupdf

    try:
        widths = []
        for data in (left, right):
            pixmap = pymupdf.Pixmap(data)
            widths.append(max(1, round(pixmap.width * height / pixmap.height)))
        total = widths[0] + gap + widths[1]
        doc = pymupdf.open()
        page = doc.new_page(width=total, height=height)
        page.draw_rect(page.rect, color=None, fill=(0.16, 0.16, 0.16))
        page.insert_image(pymupdf.Rect(0, 0, widths[0], height), stream=left)
        page.insert_image(pymupdf.Rect(widths[0] + gap, 0, total, height), stream=right)
        return page.get_pixmap(dpi=72).tobytes("png")
    except Exception as err:
        logger.debug(f"no side-by-side picture: {err}")
        return None


def redraw(picture: Picture, terminal_columns: int, up: int, tmux: bool = False) -> str:
    """After a resize: delete the old picture and draw it again at the right edge, ``up`` rows above the cursor.

    ``up`` is how far the cursor is below the first line of the question (the picture's top row).
    """
    drawn = escape(picture.png, picture.kind, picture.columns, picture.rows)
    if tmux:
        drawn = through_tmux(drawn)
    column = terminal_columns - picture.columns + 1
    rise = f"\x1b[{up}A" if up > 0 else ""
    return f"\x1b7{rise}\x1b[{column}G{drawn}\x1b8"


def draw(picture: Picture, stream=None) -> None:
    """Make room and draw ``picture`` at the right edge, beside the lines that follow."""
    stream = sys.stdout if stream is None else stream
    stream.write(place(picture.png, picture.kind, picture.columns, picture.rows, picture.terminal_columns,
                       tmux=in_tmux()))
    stream.flush()


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
    stream.write(place(png, kind, columns, rows, terminal_columns, tmux=in_tmux()))
    stream.flush()
    return rows


def clear(kind: str, stream=None) -> None:
    """Remove the pictures drawn so far (kitty protocol; iTerm2 pictures scroll away with their lines)."""
    if kind != KITTY:
        return
    stream = sys.stdout if stream is None else stream
    sequence = "\x1b_Ga=d,d=A,q=2\x1b\\"
    stream.write(through_tmux(sequence) if in_tmux() else sequence)
    stream.flush()
