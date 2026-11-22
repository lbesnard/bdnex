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
    extra: list[str] = field(default_factory=list)  # the `  - item` lines of an option whose value is a block list

    @property
    def is_option(self) -> bool:
        return not self.children and not self.line.rstrip().endswith(":")


TOP = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
CHILD = re.compile(r"^  ([A-Za-z_][\w-]*):(.*)$")
LIST_ITEM = re.compile(r"^\s+- ")


def packaged() -> list[Section]:
    """The packaged defaults, as top-level options and sections with their options, in file order."""
    items: list[Section] = []
    for raw in DEFAULTS_PATH.read_text(encoding="utf-8").splitlines():
        top, child = TOP.match(raw), CHILD.match(raw)
        if top:
            items.append(Section(top.group(1), raw))
        elif child and items:
            items[-1].children.append((child.group(1), raw))
        elif items and not items[-1].children and LIST_ITEM.match(raw):
            items[-1].extra.append(raw)
    return [item for item in items if item.name not in MANAGED]


def _has_comment_for(lines: list[str], name: str, start: int = 0, stop: Optional[int] = None) -> bool:
    pattern = re.compile(rf"^\s*#\s*{re.escape(name)}\s*:")
    return any(pattern.match(line) for line in lines[start:stop])


def _lines(text: str) -> list[str]:
    """The lines of a file, split on newlines only (``str.splitlines`` also cuts at U+2028, ``\\x85``...: inside a quoted
    password or path that would change the value when the file is joined back)."""
    lines = re.split(r"\r?\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _section_names() -> set[str]:
    return {item.name for item in packaged()}


def _header(lines: list[str], name: str) -> Optional[int]:
    """The line of the section ``name``: the active one when there is one, else a commented one."""
    active = re.compile(rf"^{re.escape(name)}:\s*(?:#.*)?$")
    commented = re.compile(rf"^#\s*{re.escape(name)}:\s*(?:#.*)?$")
    for pattern in (active, commented):
        found = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
        if found is not None:
            return found
    return None


def _span_end(lines: list[str], header: int) -> int:
    """The line after the last line of the section that starts at ``header`` (trailing blank lines excluded)."""
    sections = _section_names()
    active = re.compile(r"^[A-Za-z_][\w-]*:")
    commented = re.compile(r"^# ?([A-Za-z_][\w-]*):")  # `# write:` is a section only when bdnex has one of that name:
    end = len(lines)                                    # a user's own `# Note: ...` comment does not end the section
    for i in range(header + 1, len(lines)):
        found = commented.match(lines[i])
        if active.match(lines[i]) or (found and found.group(1) in sections):
            end = i
            break
    while end > header + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def missing(text: str, user: Optional[dict[str, Any]] = None) -> list[str]:
    """Dotted names of the options the file mentions neither as a setting nor as a comment."""
    import yaml

    user = user if user is not None else (yaml.safe_load(text) or {})
    lines = _lines(text)
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
    lines = _lines(text)
    added: list[str] = []
    for item in packaged():
        if not item.children:
            if item.name not in user and not _has_comment_for(lines, item.name):
                if lines and lines[-1].strip():
                    lines.append("")
                lines.append(comment_out(item.line))
                lines.extend(comment_out(extra) for extra in item.extra)
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


def _help_of(packaged_line: str) -> Optional[tuple[str, int]]:
    """``(# the help, its column)`` from a packaged line, ``None`` when it has none."""
    found = re.search(r"\s(#\s.*)$", packaged_line)
    return (found.group(1), found.start(1)) if found else None


_CHOICES = re.compile(r"^#\s*[\w.-]+(?:\s*\|\s*[\w.-]+)+\s*$")  # a comment that is only "a | b | c"


def _stale_choices(comment: str, help_: str) -> bool:
    """True when ``comment`` is just a list of choices (the help an older release wrote) and not what ``help_`` says now.
    A comment with any other word in it is the user's own and is never replaced."""
    return bool(_CHOICES.match(comment.strip())) and comment.strip() != help_.strip()


def annotate(text: str) -> tuple[str, list[str]]:
    """``text`` with the help comment (the possible values and what the option does) put back after every option the
    file already mentions, active or commented out, that has no comment of its own, or only an old list of choices (``# a | b``). Values are never changed.
    Returns (new text, dotted names annotated)."""
    lines = _lines(text)
    done: list[str] = []
    for item in packaged():
        if item.children:
            header = _header(lines, item.name)
            if header is None:
                continue
            scope, wanted, indent = (header + 1, _span_end(lines, header)), item.children, "  "
        else:
            scope, wanted, indent = (0, len(lines)), [(item.name, item.line)], ""
        for name, pline in wanted:
            help_ = _help_of(pline)
            if help_ is None:
                continue
            pattern = re.compile(rf"^{indent}(?:#\s*)?{re.escape(name)}:(.*)$")
            for i in range(*scope):
                found = pattern.match(lines[i])
                if not found or found.group(1).strip() == "":
                    continue
                old = re.search(r"\s(#.*)$", found.group(1))
                if old and _stale_choices(old.group(1), help_[0]):
                    lines[i] = lines[i][:len(lines[i]) - len(old.group(1))].rstrip()  # drop the old list, then add the new
                    old = None
                if not old:
                    pad = max(len(lines[i]) + 2, help_[1])
                    lines[i] = lines[i].ljust(pad) + help_[0]
                    done.append(f"{item.name}.{name}" if item.children else name)
                    break
    return "\n".join(lines) + "\n", done


def format_scalar(value: Any) -> str:
    """A YAML scalar for ``value`` (``null``, ``true``, a number, or a string quoted only when it has to be)."""
    import yaml

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return yaml.safe_dump(str(value), allow_unicode=True, default_flow_style=True, width=10 ** 6).split("\n...")[0].strip()


def _trailing_comment(rest: str) -> str:
    """The ``  # comment`` at the end of the text after ``key:`` (a ``#`` inside a quoted value is not one)."""
    rest = rest.rstrip()
    if rest[:1] in ("'", '"'):
        quote, i = rest[0], 1
        while i < len(rest):
            if quote == '"' and rest[i] == "\\":
                i += 2
                continue
            if rest[i] == quote:
                if quote == "'" and rest[i + 1:i + 2] == "'":  # '' is a quote inside single quotes
                    i += 2
                    continue
                break
            i += 1
        rest = rest[i + 1:]
        return rest.rstrip() if rest.strip().startswith("#") else ""
    found = re.search(r"([ \t]+#.*)$", rest)
    return found.group(1) if found else ""


def set_value(text: str, dotted: str, value: Any) -> str:
    """``text`` with the option ``dotted`` (``write.pdf``, or ``library``) set to the scalar ``value``.

    An active line keeps its own trailing comment; a commented-out line is made active (and so is a commented section
    line); a missing option is added under its section, and a missing section at the end. Everything else is left as it is."""
    lines = _lines(text)
    section, _, name = dotted.rpartition(".")
    indent = "  " if section else ""
    rendered = format_scalar(value)
    scope = range(len(lines))
    header = None
    if section:
        header = _header(lines, section)
        if header is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [f"{section}:", f"  {name}: {rendered}"]
            return "\n".join(lines) + "\n"
        scope = range(header + 1, _span_end(lines, header))
    gap = " " * (len(indent) + 1)  # `  # pdf:` (comment_out) or `#   pdf:` (written by hand); a deeper `#     mode:` is an example of something else
    for pattern in (rf"^{indent}{re.escape(name)}:[ \t]*(.*)$",
                    rf"^(?:{indent}#[ \t]?|#{gap}){re.escape(name)}:[ \t]*(.*)$"):
        for i in scope:
            found = re.match(pattern, lines[i])
            if found:
                lines[i] = f"{indent}{name}: {rendered}" + _trailing_comment(found.group(1))
                if header is not None and re.match(r"^#\s*", lines[header]):
                    lines[header] = re.sub(r"^#\s*", "", lines[header])  # the section has to be active too
                return "\n".join(lines) + "\n"
    if header is not None:
        lines[_span_end(lines, header):_span_end(lines, header)] = [f"{indent}{name}: {rendered}"]
        lines[header] = re.sub(r"^#\s*", "", lines[header])
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{name}: {rendered}")
    return "\n".join(lines) + "\n"


def set_aliases(text: str, items: list[str]) -> str:
    """``text`` with the active top-level ``aliases:`` written as a block list of ``items`` (``aliases: []`` when none).

    The header keeps its own trailing comment and the rest of the file is left as it is; a file without an active
    ``aliases:`` gets the block at its end (a commented ``# aliases:`` stays as the help it is)."""
    import json

    lines = _lines(text)
    block = [f"  - {json.dumps(item, ensure_ascii=False)}" for item in items]
    header = next((i for i, line in enumerate(lines) if re.match(r"^aliases:", line)), None)
    if header is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["aliases:" if block else "aliases: []", *block]
        return "\n".join(lines) + "\n"
    comment = re.match(r"^aliases:[ \t]*(?:\[.*?\])?[ \t]*(#.*)?$", lines[header])
    end = header + 1
    if re.match(r"^aliases:[ \t]*\[[^\]]*$", lines[header]):  # a flow list written over several lines: up to its `]`
        while end < len(lines) and "]" not in lines[end - 1]:
            end += 1
    while end < len(lines) and (not lines[end].strip() or re.match(r"^[ \t]+(-|#)", lines[end])):
        end += 1
    while end > header + 1 and not lines[end - 1].strip():
        end -= 1  # the blank lines after the block belong to what follows
    suffix = f"  {comment.group(1)}" if comment and comment.group(1) else ""
    lines[header:end] = [("aliases:" if block else "aliases: []") + suffix, *block]
    return "\n".join(lines) + "\n"
