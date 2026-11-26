"""A picture drawn with text: each character cell is two pixels (``▀``: the upper one is the colour of the letter, the lower one the
background). It works in any terminal, over SSH, inside a Textual screen, with no graphics protocol."""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional

from rich.text import Text


def half_blocks(image: bytes, columns: int = 36, max_rows: int = 22) -> Optional[Text]:
    """``image`` (any format PyMuPDF reads) as coloured text at most ``columns`` wide and ``max_rows`` high; ``None`` when it is
    not a picture."""
    try:
        import pymupdf as fitz
    except ImportError:  # an older PyMuPDF
        import fitz
    try:
        from bdnex.importer import covers

        covers.refuse_bomb(image)  # a header that promises gigabytes is not decoded
        source = fitz.Pixmap(image)
        if source.alpha or source.colorspace is None or source.colorspace.n != 3:
            source = fitz.Pixmap(fitz.csRGB, fitz.Pixmap(source, 0) if source.alpha else source)
        width = columns
        height = max(2, round(source.height * width / source.width))
        if height > max_rows * 2:  # tall picture: the height decides
            height = max_rows * 2
            width = max(1, round(source.width * height / source.height))
        height -= height % 2
        small = fitz.Pixmap(source, width, height)
        pixel = small.pixel
        text = Text()
        for y in range(0, height, 2):
            for x in range(width):
                top, bottom = pixel(x, y), pixel(x, y + 1)
                text.append("▀", style=f"rgb({top[0]},{top[1]},{top[2]}) on rgb({bottom[0]},{bottom[1]},{bottom[2]})")
            text.append("\n")
        return text
    except Exception:
        return None


def chafa_installed() -> bool:
    return shutil.which("chafa") is not None


def picture(image: bytes, columns: int, rows: int) -> tuple[Optional[Text], str]:
    """``image`` as text as large as ``columns`` x ``rows`` cells allow, and the name of what drew it: ``chafa`` (when it is installed: it picks
    the best characters for each cell, much sharper than half blocks) or ``half blocks``. Works over SSH and in tmux: it is only text."""
    if chafa_installed():
        try:
            done = subprocess.run(["chafa", "--format=symbols", "--colors=full", "--work=9", f"--size={columns}x{rows}", "--animate=off", "-"],
                                  input=image, capture_output=True, timeout=15)
            if done.returncode == 0 and done.stdout:
                return Text.from_ansi(done.stdout.decode("utf-8", "replace").rstrip("\n")), "chafa"
        except (OSError, subprocess.SubprocessError):
            pass  # chafa broke: the plain drawing below
    return half_blocks(image, columns, rows), "half blocks"


ZOOMS = (1, 2, 3, 4, 6, 8, 12)


def crop(image: bytes, zoom: int, px: float, py: float, columns: int, rows: int) -> tuple[bytes, float, float]:
    """The part of ``image`` that fills ``columns`` x ``rows`` cells at ``zoom`` times the whole-page size, its top-left at ``(px, py)`` (0 to 1 of the
    way it can move), and how far one step of panning moves ``px`` / ``py`` (half a window). Zoom 1 is the whole picture. This is what makes small
    writing readable: the terminal has few cells, so the way to more detail is to look at less of the page."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    if zoom <= 1:
        return image, 0.0, 0.0
    from bdnex.importer import covers

    covers.refuse_bomb(image)
    source = fitz.Pixmap(image)
    if source.alpha or source.colorspace is None or source.colorspace.n != 3:
        source = fitz.Pixmap(fitz.csRGB, fitz.Pixmap(source, 0) if source.alpha else source)
    width, height = source.width, source.height
    aspect = columns / (2.0 * rows)  # a terminal cell is about twice as tall as it is wide
    win_w = max(8, width // zoom)
    win_h = round(win_w / aspect)
    if win_h > height:
        win_h, win_w = height, min(width, round(height * aspect))
    fx = 0.5 * win_w / (width - win_w) if width > win_w else 0.0
    fy = 0.5 * win_h / (height - win_h) if height > win_h else 0.0
    x0, y0 = round(min(max(px, 0.0), 1.0) * (width - win_w)), round(min(max(py, 0.0), 1.0) * (height - win_h))
    box = fitz.IRect(x0, y0, x0 + win_w, y0 + win_h)
    part = fitz.Pixmap(source.colorspace, box, source.alpha)
    part.copy(source, box)
    return part.tobytes("png"), min(fx, 1.0), min(fy, 1.0)
