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


SUBTITLE_COLON = re.compile(r"\s*:\s+")


def parse_rules(mapping: dict[str, str]) -> list[tuple[str, "re.Pattern", str]]:
    """beets' syntax: ``"FIELD REGEX": VALUE`` -> ``(field, compiled regex, value)``, in the order written. Raises ``ValueError`` saying what is wrong."""
    rules = []
    for key, value in (mapping or {}).items():
        field, _, pattern = str(key).strip().partition(" ")
        if not field or not pattern.strip():
            raise ValueError(f"{key!r}: write it as `FIELD REGEX: VALUE` (for example `series .*Tintin.*: Tintin`)")
        try:
            rules.append((field, re.compile(pattern.strip(), re.IGNORECASE), "" if value is None else str(value)))
        except re.error as err:
            raise ValueError(f"{key!r}: bad regular expression ({err})") from None
    return rules


def apply_rewrites(fields: dict[str, Any], rules: list) -> dict[str, Any]:
    """The template fields with ``paths.rewrite`` applied (beets' ``rewrite``): the first rule of a field whose regex is found in its value
    gives it the new value, in the paths only (the library is not changed). Returns a copy."""
    out, done = dict(fields), set()
    for field, pattern, value in rules:
        if field in out and field not in done and pattern.search(str(fields.get(field, ""))):
            out[field], _ = value, done.add(field)
    return out


def collection_of(fields: dict[str, Any], path: str, rules: list) -> str:
    """The collection an album belongs to (``paths.collections``): the first rule whose regex is found in the field it names (any template
    field, or ``path`` / ``filename`` of the file), else ``""``. A family of albums the site does not group (Tintin, Astérix and their special
    issues), put together in the paths."""
    import os

    seen = {**fields, "path": path, "filename": os.path.basename(path)}
    for field, pattern, name in rules:
        if field in seen and pattern.search(str(seen[field] if seen[field] is not None else "")):
            return name
    return ""


def apply_replacements(value: str, rules: list) -> str:
    for _, pattern, replacement in rules:
        value = pattern.sub(replacement, value)
    return value


def parse_replacements(mapping: dict[str, str]) -> list[tuple[str, "re.Pattern", str]]:
    """beets' ``replace``: ``REGEX: REPLACEMENT``, applied in order to every folder and file name, before bdnex's own safety rules (which stay)."""
    rules = []
    for pattern, replacement in (mapping or {}).items():
        try:
            rules.append(("", re.compile(str(pattern)), "" if replacement is None else str(replacement)))
        except re.error as err:
            raise ValueError(f"{pattern!r}: bad regular expression ({err})") from None
    return rules


def sanitize(value: str, replace: list = ()) -> str:
    """Make one path component safe on every platform.

    A colon that opens a subtitle (``Batman: Dark Victory``) becomes `` - `` (``Batman - Dark Victory``), as a person would write it; any other
    character a file system refuses (a colon in ``12:30``, ``?``, ``*``...) becomes ``_``."""
    value = apply_replacements(str(value), replace) if replace else str(value)
    value = SUBTITLE_COLON.sub(" - ", value)
    value = ILLEGAL.sub("_", value)
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
        "collection": "",  # from `paths.collections` (the organizer fills it: it needs the file's path)
    }


FIELD_NAMES = ("kind", "series", "title", "volume", "special", "language", "year", "publisher", "isbn", "genre", "authors",
               "writer", "penciller", "colorist", "collection")  # what `fields_for` provides (a test keeps the two together)


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


def render(template: str, values: dict[str, Any], resolve_missing: Callable[[str], Optional[str]], replace: list = ()) -> Rendered:
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
        return sanitize(value, replace)

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
                parts.append(sanitize(rendered, replace))
    except MissingField as err:
        return Rendered([], skipped_field=err.field)
    return Rendered(parts)
