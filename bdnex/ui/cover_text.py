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
            done = subprocess.run(["chafa", "--format=symbols", "--colors=full", f"--size={columns}x{rows}", "--animate=off", "-"],
                                  input=image, capture_output=True, timeout=15)
            if done.returncode == 0 and done.stdout:
                return Text.from_ansi(done.stdout.decode("utf-8", "replace").rstrip("\n")), "chafa"
        except (OSError, subprocess.SubprocessError):
            pass  # chafa broke: the plain drawing below
    return half_blocks(image, columns, rows), "half blocks"
