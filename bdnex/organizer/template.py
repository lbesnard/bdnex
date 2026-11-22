"""Path templates: ``Series/$series/$series - T$volume:02 - $title``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

VAR = re.compile(r"\$(?:\{(?P<b>\w+)\}|(?P<n>\w+))(?::(?P<pad>\d+))?")
ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_COMPONENT = 120


class MissingField(Exception):
    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


def sanitize(value: str) -> str:
    """Make one path component safe on every platform."""
    value = ILLEGAL.sub("_", str(value))
    value = re.sub(r"\s+", " ", value).strip().rstrip(". ")
    return value[:MAX_COMPONENT].rstrip(". ") or "_"


TRAILING_YEAR = re.compile(r"\s*\((?:1[5-9]|20)\d{2}\)\s*$")


def strip_year(series: str) -> str:
    """``Batman: Dark Victory (1999)`` -> ``Batman: Dark Victory``: the site adds the year of its first album to a series name that another series
    has too. Never leaves nothing (a series called ``(1999)`` keeps it)."""
    return TRAILING_YEAR.sub("", series) or series


def fields_for(album: dict[str, Any]) -> dict[str, Any]:
    """Template variables for an album (``Library.get_album``)."""
    credits = album["credits"]
    authors = []
    for role in ("Writer", "Penciller"):
        for name in credits.get(role, []):
            if name not in authors:
                authors.append(name)
    volume = album.get("volume")
    return {
        "kind": album["kind"],
        "series": album.get("series") or "",
        "title": album["title"],
        "volume": volume if volume is not None else (album.get("alternate_number") or ""),
        "special": album.get("alternate_number") or "",  # an intégrale (INT), hors-série (HS)...: what the site calls the tome when it is no number
        "language": album.get("language") or "",
        "year": album.get("year") or "",
        "publisher": album.get("publisher") or "",
        "isbn": album.get("isbn") or "",
        "genre": (album["genres"] or [""])[0],
        "authors": ", ".join(authors),
        "writer": ", ".join(credits.get("Writer", [])),
        "penciller": ", ".join(credits.get("Penciller", [])),
        "colorist": ", ".join(credits.get("Colorist", [])),
    }


FIELD_NAMES = ("kind", "series", "title", "volume", "special", "language", "year", "publisher", "isbn", "genre", "authors",
               "writer", "penciller", "colorist")  # what `fields_for` provides (a test keeps the two together)


FUNCTION = re.compile(r"%(\w+)\{")
FUNCTIONS = ("upper", "lower", "title", "capitalize", "left", "right", "if", "ifdef", "asciify", "first")


def unknown_fields(template: str) -> list[str]:
    """The ``$variables`` of a template that bdnex does not know (a typo, usually), and the ``%functions`` too."""
    names = {name for name in template_fields(template) if name not in FIELD_NAMES}
    names |= {f"%{name}" for name in FUNCTION.findall(template) if name not in FUNCTIONS}
    for call in re.finditer(r"%ifdef\{\s*(\w+)", template):  # %ifdef names a field without a $
        if call.group(1) not in FIELD_NAMES:
            names.add(call.group(1))
    return sorted(names)


def template_fields(template: str) -> list[str]:
    return [m.group("b") or m.group("n") for m in VAR.finditer(template)]


def _arguments(text: str, start: int) -> tuple[list[str], int]:
    """The comma-separated arguments of a ``%func{...}`` whose ``{`` is just before ``start``: ``(arguments, index after })``.
    Commas and braces of nested calls stay inside their argument."""
    depth, args, current, i = 1, [], [], start
    while i < len(text):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                args.append("".join(current))
                return args, i + 1
        elif char == "," and depth == 1:
            args.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    raise ValueError("a %function{ is never closed")


def _truthy(text: str) -> bool:
    return text.strip().lower() not in ("", "0", "false")


def _call(name: str, args: list[str]) -> str:
    """Apply a template function to its (already worked out) arguments."""
    text = args[0] if args else ""
    if name == "upper":
        return text.upper()
    if name == "lower":
        return text.lower()
    if name == "title":
        return text.title()
    if name == "capitalize":
        return text[:1].upper() + text[1:].lower()
    if name in ("left", "right"):
        count = int(args[1]) if len(args) > 1 and args[1].strip().isdigit() else len(text)
        return text[:count] if name == "left" else (text[-count:] if count else "")
    if name == "asciify":
        import unicodedata

        return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).encode("ascii", "ignore").decode()
    if name == "first":  # %first{text,count,skip,sep,join}: some of the items of a list
        count = int(args[1]) if len(args) > 1 and args[1].strip().isdigit() else 1
        skip = int(args[2]) if len(args) > 2 and args[2].strip().isdigit() else 0
        sep = args[3] if len(args) > 3 and args[3] else ", "
        join = args[4] if len(args) > 4 and args[4] else ", "
        return join.join([item.strip() for item in text.split(sep) if item.strip()][skip:skip + count])
    raise ValueError(f"unknown function %{name}")


@dataclass
class Rendered:
    parts: list[str]  # sanitised path components (no extension)
    skipped_field: Optional[str] = None


def render(template: str, values: dict[str, Any], resolve_missing: Callable[[str], Optional[str]]) -> Rendered:
    """Render a template into path components.

    ``resolve_missing(field)`` is called for an empty field and returns the value to
    use, or ``None`` to skip the file. Values are sanitised, so a ``/`` inside a
    value can never create a directory.
    """
    cache: dict[str, Optional[str]] = {}

    def substitute(match: re.Match, lenient: bool = False) -> str:
        name = match.group("b") or match.group("n")
        value = values.get(name, "")
        if value in ("", None):
            if lenient:
                return ""  # asking whether a field is empty must not ask for it
            if name not in cache:
                cache[name] = resolve_missing(name)
            if cache[name] is None:
                raise MissingField(name)
            value = cache[name]
        pad = match.group("pad")
        if pad and str(value).isdigit():
            value = str(int(value)).zfill(int(pad))
        return sanitize(value)

    def expand(text: str, lenient: bool = False) -> str:
        """``$variables`` and ``%functions{...}`` of a piece of template (``if`` and ``ifdef`` work out only the branch they take)."""
        out, i = [], 0
        while i < len(text):
            call = FUNCTION.match(text, i)
            if call and call.group(1) in FUNCTIONS:
                args, i = _arguments(text, call.end())
                name = call.group(1)
                if name == "if":
                    branch = 1 if _truthy(expand(args[0], lenient=True)) else 2
                    out.append(expand(args[branch], lenient) if branch < len(args) else "")
                elif name == "ifdef":
                    defined = values.get(args[0].strip(), "") not in ("", None)
                    branch = 1 if defined else 2
                    out.append(expand(args[branch], lenient) if branch < len(args) else "")
                else:
                    out.append(_call(name, [expand(a, lenient) for a in args]))
                continue
            var = VAR.match(text, i)
            if var:
                out.append(substitute(var, lenient))
                i = var.end()
                continue
            out.append(text[i])
            i += 1
        return "".join(out)

    parts = []
    try:
        for component in template.split("/"):
            rendered = expand(component).strip()
            if rendered:
                parts.append(sanitize(rendered))
    except MissingField as err:
        return Rendered([], skipped_field=err.field)
    return Rendered(parts)
