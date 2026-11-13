"""Show ComicInfo differences the way `git diff` does: red = in the file, green = in the database."""

from __future__ import annotations

import difflib
import re

from bdnex.organizer.comicinfo import Change
from bdnex.ui.color import paint

TOKEN = re.compile(r"\w+|\s+|[^\w\s]")


def highlight(old: str, new: str, on: bool) -> tuple[str, str]:
    """``old`` and ``new`` with only the words that differ emphasised."""
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    out_old, out_new = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        piece_old, piece_new = "".join(a[i1:i2]), "".join(b[j1:j2])
        if op == "equal":
            out_old.append(piece_old)
            out_new.append(piece_new)
        else:
            out_old.append(paint(piece_old, "red_bold", on))
            out_new.append(paint(piece_new, "green_bold", on))
    return "".join(out_old), "".join(out_new)


def render(changes: list[Change], on: bool) -> list[str]:
    """Lines for a list of changes, field names aligned."""
    width = max((len(c.field) for c in changes), default=0)
    lines: list[str] = []
    for c in changes:
        name = c.field.ljust(width)
        note = paint(f"  {c.note}", "dim", on) if c.note else ""
        if c.kind == "+":
            lines.append(paint(f"  + {name}  {c.after}", "green", on) + note)
        else:
            old, new = highlight(c.before or "", c.after or "", on)
            lines.append(paint(f"  ~ {c.field}", "yellow", on) + note)
            lines.append(paint("      - ", "red", on) + old)
            lines.append(paint("      + ", "green", on) + new)
    return lines


def legend(on: bool) -> str:
    return (paint("red", "red", on) + " = in the file   " + paint("green", "green", on)
            + " = in the database   (only changed words are bold; other fields of the file are kept)")


def conflict_text(path: str, changes: list[Change], on: bool) -> str:
    head = paint(path, "bold", on) + "\nComicInfo.xml in the file differs from the database:\n" + legend(on)
    return head + "\n" + "\n".join(render(changes, on))
