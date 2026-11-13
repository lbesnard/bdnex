"""Keep the user's config file complete: every option, commented out until it is wanted.

The packaged ``conf/bdnex.yaml`` is the one place that lists every option, its default and what it means. A
user file only needs the options it changes, but it is easier to work with when it shows all of them. These
functions add the options a file does not mention yet, as comments, without touching anything else:

* an option already in the file (active, or commented out) is left alone;
* a missing option is added as a comment under its section, in the packaged file's own words;
* a section the file lacks is appended, commented out, at the end.

Uncommenting an option (and its section line, if that is commented too) is all it takes to use it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from bdnex.config import DEFAULTS_PATH

MANAGED = {"config_version", "collection_path"}  # written by bdnex itself, always active


@dataclass
class Section:
    name: str
    line: str  # the section line of the packaged file (`match:`), or the whole line of a top-level option
    children: list[tuple[str, str]] = field(default_factory=list)  # (option, its line)

    @property
    def is_option(self) -> bool:
        return not self.children and not self.line.rstrip().endswith(":")


TOP = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
CHILD = re.compile(r"^  ([A-Za-z_][\w-]*):(.*)$")


def packaged() -> list[Section]:
    """The packaged defaults, as top-level options and sections with their options, in file order."""
    items: list[Section] = []
    for raw in DEFAULTS_PATH.read_text(encoding="utf-8").splitlines():
        top, child = TOP.match(raw), CHILD.match(raw)
        if top:
            items.append(Section(top.group(1), raw))
        elif child and items:
            items[-1].children.append((child.group(1), raw))
    return [item for item in items if item.name not in MANAGED]


def _has_comment_for(lines: list[str], name: str, start: int = 0, stop: Optional[int] = None) -> bool:
    pattern = re.compile(rf"^\s*#\s*{re.escape(name)}\s*:")
    return any(pattern.match(line) for line in lines[start:stop])


def _header(lines: list[str], name: str) -> Optional[int]:
    pattern = re.compile(rf"^(?:#\s*)?{re.escape(name)}:\s*(?:#.*)?$")
    return next((i for i, line in enumerate(lines) if pattern.match(line)), None)


def _span_end(lines: list[str], header: int) -> int:
    """The line after the last line of the section that starts at ``header`` (trailing blank lines excluded)."""
    top_level = re.compile(r"^(?:#\s*)?[A-Za-z_][\w-]*:")
    end = len(lines)
    for i in range(header + 1, len(lines)):
        if top_level.match(lines[i]):
            end = i
            break
    while end > header + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def missing(text: str, user: Optional[dict[str, Any]] = None) -> list[str]:
    """Dotted names of the options the file mentions neither as a setting nor as a comment."""
    import yaml

    user = user if user is not None else (yaml.safe_load(text) or {})
    lines = text.splitlines()
    absent = []
    for item in packaged():
        if not item.children:
            if item.name not in user and not _has_comment_for(lines, item.name):
                absent.append(item.name)
            continue
        header = _header(lines, item.name)
        have = user.get(item.name) if isinstance(user.get(item.name), dict) else {}
        for name, _ in item.children:
            if name in have:
                continue
            if header is not None and _has_comment_for(lines, name, header + 1, _span_end(lines, header)):
                continue
            absent.append(f"{item.name}.{name}")
    return absent


def comment_out(line: str) -> str:
    """``  key: value  # help`` -> ``  # key: value  # help`` (the indent stays, so uncommenting restores it)."""
    indent = len(line) - len(line.lstrip(" "))
    return " " * indent + "# " + line.lstrip(" ")


def complete(text: str) -> tuple[str, list[str]]:
    """``text`` with every missing option added as a comment. Returns (new text, dotted names added)."""
    import yaml

    user = yaml.safe_load(text) or {}
    lines = text.splitlines()
    added: list[str] = []
    for item in packaged():
        if not item.children:
            if item.name not in user and not _has_comment_for(lines, item.name):
                if lines and lines[-1].strip():
                    lines.append("")
                lines.append(comment_out(item.line))
                added.append(item.name)
            continue
        header = _header(lines, item.name)
        have = user.get(item.name) if isinstance(user.get(item.name), dict) else {}
        end = _span_end(lines, header) if header is not None else None
        new = []
        for name, line in item.children:
            if name in have:
                continue
            if header is not None and _has_comment_for(lines, name, header + 1, end):
                continue
            new.append(comment_out(line))
            added.append(f"{item.name}.{name}")
        if not new:
            continue
        if header is not None:
            lines[end:end] = new
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"# {item.name}:")
            lines.extend(new)
    return "\n".join(lines) + "\n", added
