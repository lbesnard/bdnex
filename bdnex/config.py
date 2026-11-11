"""Configuration: packaged defaults, user overrides, validation.

* The packaged ``conf/bdnex.yaml`` holds every option with its default.
* The user file (``~/.config/bdnex/bdnex.yaml``, or ``$BDNEX_CONFIG``) only needs
  the options it overrides; it is deep-merged on top of the defaults.
* The merged result is validated with pydantic. Unknown keys, wrong types and
  out-of-range values are reported by key name. A user file written by an older
  bdnex (lower ``config_version``) is reported as *stale*: its unknown keys
  become warnings instead of errors.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONFIG_VERSION = 2
PLACEHOLDER_DIRECTORY = "/path/to/comics/library"
CONF_DIR = Path(__file__).resolve().parent / "conf"
DEFAULTS_PATH = CONF_DIR / "bdnex.yaml"

# Sections whose content is free-form and never checked for unknown keys.
FREE_FORM = {("bedetheque", "fetcher_endpoint")}

USER_TEMPLATE = """\
# bdnex configuration. Only list what you want to change: every other option
# takes its default. Run `bdnex config --defaults` to see them all.
config_version: {version}

# REQUIRED before `bdnex import`: the folder that holds (or will hold) your comics.
directory: {placeholder}

# library: ~/.local/share/bdnex/bdnex.sqlite
# import:
#   mode: in_place        # in_place | copy | move
# paths:
#   series: 'Series/$series/$series - T$volume:02 - $title'
"""


class ConfigError(Exception):
    """The configuration is invalid; the message lists every problem."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Match(_Section):
    strong_threshold: float = Field(ge=0, le=100)
    medium_threshold: float = Field(ge=0, le=100)


class Import(_Section):
    mode: Literal["in_place", "copy", "move"]
    resume: bool


class Duplicates(_Section):
    keys: list[Literal["series", "volume", "title"]] = Field(min_length=1)
    action: Literal["ask", "skip", "keep_both", "replace"]


class Write(_Section):
    cbr: Literal["convert", "skip"]
    pdf: Literal["convert", "skip"]
    pdf_mode: Literal["auto", "extract", "render"]
    pdf_dpi: int = Field(ge=72, le=1200)
    delete_original: Literal["ask", "yes", "no"]
    conflict: Literal["ask", "file", "database"]


class Paths(_Section):
    oneshot: str
    series: str
    missing_field: Literal["ask", "placeholder", "skip"]
    placeholder: str


class Bedetheque(_Section):
    delay: tuple[float, float]
    sitemap_delay: tuple[float, float]
    pages_dir: str
    fetcher: Optional[str] = None
    fetcher_endpoint: Optional[str] = None


class Settings(_Section):
    config_version: int = Field(ge=0)
    directory: str
    library: str
    match: Match
    # "import" is a Python keyword
    import_: Import = Field(alias="import")
    duplicates: Duplicates
    write: Write
    paths: Paths
    bedetheque: Bedetheque

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @property
    def library_path(self) -> Path:
        return Path(os.path.expanduser(self.library))

    @property
    def pages_dir(self) -> Path:
        return Path(os.path.expanduser(self.bedetheque.pages_dir))

    def require_library_directory(self) -> Path:
        """The managed library root; refuses the untouched placeholder."""
        if not self.directory or self.directory == PLACEHOLDER_DIRECTORY:
            raise ConfigError(
                "`directory` is not set in your bdnex config: edit it "
                f"(`bdnex config --edit`) and set it to your comics folder.")
        return Path(os.path.expanduser(self.directory))


@dataclass
class LoadedConfig:
    settings: Settings
    path: Path
    first_run: bool = False
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # merged dict, for legacy callers


def user_config_path() -> Path:
    override = os.environ.get("BDNEX_CONFIG")
    if override:
        return Path(os.path.expanduser(override))
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "bdnex" / "bdnex.yaml"


def load_defaults() -> dict[str, Any]:
    with open(DEFAULTS_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def deep_merge(base: dict, override: dict) -> dict:
    """Return ``base`` with ``override`` merged in; dicts merge, anything else replaces."""
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def find_unknown_keys(user: Any, defaults: Any, prefix: tuple = ()) -> list[str]:
    """Dotted names of user keys that do not exist in the defaults."""
    if not isinstance(user, dict) or not isinstance(defaults, dict) or prefix in FREE_FORM:
        return []
    unknown = []
    for key, value in user.items():
        dotted = ".".join(prefix + (str(key),))
        if key not in defaults:
            unknown.append(dotted)
        else:
            unknown.extend(find_unknown_keys(value, defaults[key], prefix + (str(key),)))
    return unknown


def _drop_keys(data: dict, dotted_keys: list[str]) -> dict:
    data = copy.deepcopy(data)
    for dotted in dotted_keys:
        parts = dotted.split(".")
        node = data
        for part in parts[:-1]:
            node = node.get(part, {})
        node.pop(parts[-1], None)
    return data


def _format_errors(err: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in err.errors() if e["type"] != "extra_forbidden"]  # reported as unknown keys


def build_settings(user: Optional[dict]) -> tuple[Settings, list[str], dict]:
    """Validate ``defaults + user``. Returns (settings, warnings, merged_dict).

    Raises :class:`ConfigError` listing every problem.
    """
    user = user or {}
    if not isinstance(user, dict):
        raise ConfigError("the config file must contain a YAML mapping (key: value)")
    defaults = load_defaults()
    warnings: list[str] = []
    errors: list[str] = []

    version = user.get("config_version", 0)
    if not isinstance(version, int) or isinstance(version, bool):
        errors.append("config_version: must be an integer")
        version = CONFIG_VERSION
    stale = version < CONFIG_VERSION

    unknown = find_unknown_keys(user, defaults)
    if unknown:
        if stale:
            warnings.append("ignored keys no longer used: " + ", ".join(unknown))
            user = _drop_keys(user, unknown)
        else:
            errors.extend(f"{k}: unknown option (typo?)" for k in unknown)
    if stale:
        warnings.insert(0, (
            f"your config is stale (config_version {version}, current {CONFIG_VERSION}): "
            "review it with `bdnex config --defaults` and set `config_version: "
            f"{CONFIG_VERSION}` once done"))

    merged = deep_merge(defaults, user)
    merged["config_version"] = version
    try:
        settings = Settings.model_validate(merged)
    except ValidationError as err:
        errors.extend(_format_errors(err))
    if errors:
        raise ConfigError("invalid bdnex config:\n  - " + "\n  - ".join(errors))
    return settings, warnings, merged


def ensure_user_config(path: Optional[Path] = None) -> tuple[Path, bool]:
    """Create the commented template if the user file is missing. Returns (path, created)."""
    path = Path(path) if path else user_config_path()
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(USER_TEMPLATE.format(version=CONFIG_VERSION, placeholder=PLACEHOLDER_DIRECTORY),
                    encoding="utf-8")
    return path, True


def load_config(path: Optional[Path] = None) -> LoadedConfig:
    """Load (creating on first run), merge and validate the user configuration."""
    path, created = ensure_user_config(path)
    with open(path, encoding="utf-8") as fh:
        try:
            user = yaml.safe_load(fh) or {}
        except yaml.YAMLError as err:
            raise ConfigError(f"{path} is not valid YAML: {err}") from err
    try:
        settings, warnings, merged = build_settings(user)
    except ConfigError as err:
        raise ConfigError(f"{path}: {err}") from err
    return LoadedConfig(settings=settings, path=path, first_run=created, warnings=warnings, raw=merged)


FIRST_RUN_MESSAGE = (
    "Created a new bdnex configuration file: {path}\n"
    "Please edit it before importing: at least set `directory` to your comics folder\n"
    "(`bdnex config --edit` opens it, `bdnex config --defaults` lists every option).")
