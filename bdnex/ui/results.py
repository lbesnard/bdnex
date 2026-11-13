"""How every command shows a result: one colour per kind of outcome, one shape for a block.

``● written  Name`` (the label may be empty) then indented lines, a rule and a summary. Green is done, yellow is put off or odd, red failed,
cyan is a preview or a choice, dim is nothing to do. A command names the outcome; it never picks a colour.
"""

from __future__ import annotations

from bdnex.ui import color

# outcome or kind -> a style of color.CODES
STYLES = {
    # done
    "written": "green_bold", "moved": "green_bold", "copied": "green_bold", "repaired": "green_bold",
    "done": "green_bold", "fixed": "green_bold", "back": "green_bold", "found": "green_bold", "updated": "green_bold",
    "tagged": "green",
    # put off, or odd
    "skipped": "yellow", "partial": "yellow", "problem": "yellow", "ambiguous": "yellow", "no stored page": "yellow",
    # failed
    "failed": "red_bold", "lost": "red_bold",
    # a preview, or a group to look at
    "pretend": "cyan_bold", "would do": "cyan_bold", "would update": "cyan_bold", "would move back": "cyan_bold",
    "duplicate": "cyan", "group": "cyan",
    # nothing to do
    "unchanged": "dim", "already": "dim", "note": "dim",
}
SYMBOL = "●"


def style(outcome: str) -> str:
    """The colour name of an outcome (dim for one nobody listed)."""
    return STYLES.get(outcome, "dim")


def head(label: str, title: str, on: bool, kind: str = "", counter: str = "") -> str:
    """``\\n[3/9] ● label  title``: a blank line, the counter, the symbol and label in the outcome's colour, the title bold."""
    tone = style(kind or label)
    count = f"{color.paint(counter, 'dim', on)} " if counter else ""
    word = f"{color.paint(label, tone, on)}  " if label else ""
    return f"\n{count}{color.paint(SYMBOL, tone, on)} {word}{color.paint(title, 'bold', on)}"


def block(label: str, title: str, lines, on: bool, kind: str = "", counter: str = "") -> str:
    """A result: its head, then one indented line each."""
    return "\n".join([head(label, title, on, kind, counter), *(f"    {line}" for line in lines)])


def footer(parts: list, on: bool, lead: str = "") -> str:
    """A rule, then the counts: ``parts`` is ``[(text, outcome)]``; ``lead`` opens the line (``12 album(s): ``)."""
    shown = ", ".join(color.paint(text, style(kind), on) for text, kind in parts)
    return f"\n{color.rule()}\n{lead}{shown}"
