"""A picture drawn with text: each character cell is two pixels (``▀``: the upper one is the colour of the letter, the lower one the
background). It works in any terminal, over SSH, inside a Textual screen, with no graphics protocol."""

from __future__ import annotations

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
