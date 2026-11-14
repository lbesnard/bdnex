"""The settings page: every option with its kind, default and help, and a checked way to change one.

A change edits the user's own file line by line (comments stay), is validated as a whole before anything is written,
keeps the previous file as ``.bak`` and is applied to the running server at once (except the server's own options)."""

from __future__ import annotations

import os
import shutil
import typing
from pathlib import Path
from typing import Any

import yaml

from bdnex.config import ConfigError, Settings, build_settings, load_defaults
from bdnex.config_template import _help_of, packaged, set_value

HIDDEN = {"api.token", "api.password", "bedetheque.fetcher_endpoint"}  # secrets: never sent to the page
FIXED = {"collection_path", "library"}  # where the volumes are: set in the file, not from the page


def _kind(annotation: Any) -> tuple[str, list, bool]:
    """``(kind, choices, nullable)``; kind is choice | bool | int | float | text | complex."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union:
        inner = [a for a in args if a is not type(None)]
        if len(inner) == 1:
            kind, choices, _ = _kind(inner[0])
            return kind, choices, True
        if all(a is bool or typing.get_origin(a) is typing.Literal for a in inner):  # true | false | auto
            return "choice", [c for a in inner for c in ([True, False] if a is bool else typing.get_args(a))], False
        return "complex", [], False
    if origin is typing.Literal:
        return "choice", list(args), False
    if annotation is bool:
        return "bool", [], False
    if annotation is int:
        return "int", [], False
    if annotation is float:
        return "float", [], False
    if annotation is str:
        return "text", [], False
    return "complex", [], False


def _limits(field) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for item in field.metadata:
        for key, name in (("ge", "min"), ("gt", "min"), ("le", "max"), ("lt", "max")):
            if hasattr(item, key):
                found[name] = getattr(item, key)
    return found


def _value(settings: Settings, section: str, name: str) -> Any:
    holder = getattr(settings, "import_" if section == "import" else section) if section else settings
    return getattr(holder, "import_" if name == "import" else name)


def describe(settings: Settings, text: str) -> list[dict[str, Any]]:
    """The sections of the page, from the packaged defaults (their order and their help)."""
    mine = yaml.safe_load(text) or {}
    defaults = load_defaults()
    sections, general = [], []
    for item in packaged():
        if item.children:
            owner = Settings.model_fields["import_" if item.name == "import" else item.name].annotation
            options = [(item.name, name, line, owner.model_fields.get(name)) for name, line in item.children]
        else:
            options = [("", item.name, item.line, Settings.model_fields.get(item.name))]
        listed = []
        for section, name, line, field in options:
            key = f"{section}.{name}" if section else name
            if key in HIDDEN or field is None:
                continue
            kind, choices, nullable = _kind(field.annotation)
            default = (defaults.get(section) or {}).get(name) if section else defaults.get(name)
            help_ = _help_of(line)
            current = _value(settings, section, name)
            listed.append({
                "key": key, "kind": kind, "choices": choices, "nullable": nullable, **_limits(field),
                "value": current if kind != "complex" else None,
                "default": default if kind != "complex" else None,
                "help": help_[0].lstrip("# ").strip() if help_ else "",
                "yours": (name in (mine.get(section) or {})) if section else (name in mine),
                "editable": kind != "complex" and key not in FIXED,
                "restart": section == "api"})
        if listed and item.children:
            sections.append({"name": item.name, "options": listed})
        else:
            general += listed  # the options that are not in a section: library, source...
    return ([{"name": "general", "options": general}] if general else []) + sections


def _coerce(option: dict[str, Any], value: Any) -> Any:
    kind = option["kind"]
    if value is None or (isinstance(value, str) and value == "" and kind != "text"):
        if option["nullable"] or value is None:
            return None
        raise ValueError(f"{option['key']}: a value is needed")
    if kind == "choice":
        for choice in option["choices"]:
            if value == choice and type(value) is type(choice):
                return choice
        if isinstance(value, str) and value.lower() in ("true", "false") and True in option["choices"]:
            return value.lower() == "true"  # the page sends a choice as text
        return value if isinstance(value, str) else str(value)  # not one of them: reported by the caller
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{option['key']}: true or false")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)) and not (isinstance(value, str) and value.strip().lstrip("-").isdigit()):
            raise ValueError(f"{option['key']}: a whole number")
        return int(value)
    if kind == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{option['key']}: a number") from None
    if isinstance(value, (dict, list)):
        raise ValueError(f"{option['key']}: text")
    return str(value)


def change(settings: Settings, path: Path, changes: dict[str, Any], apply: bool) -> dict[str, Any]:
    """Validate ``changes`` (``{"write.pdf": "tag"}``) against the file at ``path``; with ``apply`` write the file and update
    ``settings``. Raises ``ValueError`` (a bad option or value, with the reason) or ``ConfigError``."""
    text = path.read_text(encoding="utf-8")
    known = {option["key"]: option for section in describe(settings, text) for option in section["options"]}
    lines = []
    for key, raw in changes.items():
        option = known.get(key)
        if option is None:
            raise ValueError(f"{key}: not an option you can change here")
        if not option["editable"]:
            raise ValueError(f"{key}: change it in the configuration file")
        value = _coerce(option, raw)
        if option["kind"] == "choice" and value not in option["choices"]:
            raise ValueError(f"{key}: one of {', '.join(map(str, option['choices']))}")
        for bound, test in (("min", lambda v, b: v >= b), ("max", lambda v, b: v <= b)):
            if bound in option and value is not None and not test(value, option[bound]):
                raise ValueError(f"{key}: must be {'at least' if bound == 'min' else 'at most'} {option[bound]}")
        text = set_value(text, key, value)
        lines.append(f"{key}: {option['value']!r} -> {value!r}")
    try:
        new_settings, _, _ = build_settings(yaml.safe_load(text) or {})
    except ConfigError as err:
        raise ValueError(str(err)) from err
    restart = sorted({key for key in changes if key.startswith("api.")})
    if apply:
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        for name in type(settings).model_fields:  # the running server follows at once (its closures hold this object)
            setattr(settings, name, getattr(new_settings, name))
    return {"changes": lines, "restart": restart, "stored": apply}
