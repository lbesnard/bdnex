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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CONFIG_VERSION = 2
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")  # `api.host` values that do not leave this machine
PLACEHOLDER_COLLECTION = "/path/to/comics/library"
CONF_DIR = Path(__file__).resolve().parent / "conf"
DEFAULTS_PATH = CONF_DIR / "bdnex.yaml"

# Options that were renamed: old key -> new key (the old one still works, with a warning).
RENAMED_KEYS = {"directory": "collection_path"}

# Top-level options that existed once: key -> config_version that removed it.
REMOVED_KEYS = {"bdnex": 2}

# Sections whose content is free-form and never checked for unknown keys.
FREE_FORM: set[tuple[str, ...]] = {("plugins",)}

USER_TEMPLATE = """\
# bdnex configuration. Only the options you change need to be active: every other option takes its default.
# They are all listed below, commented out (`bdnex config --update` adds the ones new versions bring).
# To change one, remove its leading `# ` (and the `# ` of its section line, such as `# write:`).
config_version: {version}

# REQUIRED before `bdnex import`: the folder that holds (or will hold) your comics.
collection_path: {placeholder}
"""


class ConfigError(Exception):
    """The configuration is invalid; the message lists every problem."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)  # a value changed later is checked too


class Match(_Section):
    use_embedded: bool
    strong_threshold: float = Field(ge=0, le=100)
    medium_threshold: float = Field(ge=0, le=100)


class Source(_Section):
    """A folder whose comics are copied or moved into the collection (``import.sources``)."""
    path: str = Field(min_length=1)
    mode: Literal["copy", "move"] = "copy"
    name: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")  # how the library calls it

    @property
    def alias(self) -> str:
        return self.name or (re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.basename(os.path.expanduser(self.path).rstrip("/"))
                                    ).strip("-").lower() or "source")

    @property
    def folder(self) -> str:
        return os.path.abspath(os.path.expanduser(self.path)).rstrip("/")


class Import(_Section):
    mode: Literal["in_place", "copy", "move"]
    resume: bool
    auto: bool
    workers: int = Field(ge=0)
    lookahead: int = Field(ge=1)
    outside: Literal["ask", "move", "copy", "leave"]
    series: Literal["ask", "always", "never"]
    thumbnails: Literal["auto", "kitty", "iterm2", "never"]
    sources: list[Source]

    @field_validator("sources")
    @classmethod
    def _distinct_sources(cls, value):
        aliases = [source.alias for source in value]
        if len(set(aliases)) != len(aliases):
            raise ValueError("two sources have the same name (the folder's name by default): give one a `name`")
        folders = [source.folder for source in value]
        if len(set(folders)) != len(folders):
            raise ValueError("a source is listed twice")
        return value


class Duplicates(_Section):
    keys: list[Literal["series", "volume", "title"]] = Field(min_length=1)
    action: Literal["ask", "skip", "keep_both", "replace"]
    prefer: list[Literal["cbz", "cbr", "pdf"]] = Field(min_length=1)
    versions_dir: str = Field(min_length=1)


class Trash(_Section):
    dir: str = Field(min_length=1)


class Watch(_Section):
    enabled: bool
    interval: int = Field(ge=1, le=1440)  # minutes between two looks at the import sources


class Cover(_Section):
    download: bool
    dir: str = Field(min_length=1)
    check: Union[bool, Literal["auto"]]  # auto: whenever OpenCV is installed
    match_percentage: float = Field(ge=0, le=100)


class Write(_Section):
    format_preferred: Literal["cbz", "none"]
    cbr: Literal["convert", "skip"]
    pdf: Literal["convert", "tag", "skip"]
    pdf_mode: Literal["auto", "extract", "render"]
    pdf_dpi: int = Field(ge=72, le=1200)
    pdf_render_format: Literal["auto", "png", "jpeg"]
    pdf_jpeg_quality: int = Field(ge=1, le=100)
    delete_original: Literal["ask", "yes", "no"]
    use_trash: bool
    min_free_space: int = Field(ge=0)
    conflict: Literal["ask", "file", "database"]
    corrupted: Literal["ask", "leave", "move"]
    size_tolerance: float = Field(ge=0, le=1000)
    size_change: Literal["ask", "accept", "refuse"]
    workers: int = Field(ge=0, le=16)  # files written at once by write / move; 0: the cores less one, at most 4

    @field_validator("delete_original", mode="before")
    @classmethod
    def _yaml_yes_no(cls, value):
        """YAML reads an unquoted ``yes`` / ``no`` as true / false: accept them as the words they were."""
        if value is True:
            return "yes"
        if value is False:
            return "no"
        return value


class Api(_Section):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    token: Optional[str] = None
    username: str = Field(min_length=1)  # with `password`: the login a browser asks for (HTTP Basic)
    password: Optional[str] = None
    session_hours: float = Field(gt=0, le=24 * 365)  # how long a browser stays logged in
    behind_proxy: bool  # true: the client address is the first `X-Forwarded-For` (set it only behind your own proxy)
    allow_changes: bool  # the web page (and the API) may start jobs and change the library; read-only otherwise


class PathRule(_Section):
    match: list[str] = Field(min_length=1)  # `bdnex ls` terms, all must match the album
    template: str = Field(min_length=1)

    @field_validator("match", mode="before")
    @classmethod
    def _one_or_many(cls, value):
        return [value] if isinstance(value, str) else value

    @field_validator("match")
    @classmethod
    def _terms_parse(cls, value):
        """Every term must be valid `bdnex ls` syntax (a bad regular expression, an unknown number...)."""
        from bdnex.library.query import QueryError, compile_query

        try:
            compile_query(value)
        except QueryError as err:
            raise ValueError(f"{err}") from None
        return value

    @field_validator("template")
    @classmethod
    def _known_variables(cls, value):
        return _check_template(value)


def _check_template(value: str) -> str:
    """A path template may only use the variables bdnex has (``$series``, ``$title``...)."""
    from bdnex.organizer.template import FIELD_NAMES, unknown_fields

    unknown = unknown_fields(value)
    if unknown:
        raise ValueError(f"unknown variable(s) {', '.join('$' + name for name in unknown)} in {value!r}; "
                         f"the known ones are {', '.join('$' + name for name in FIELD_NAMES)}")
    return value


class Paths(_Section):
    oneshot: str
    series: str
    rules: list[PathRule]

    @field_validator("oneshot", "series")
    @classmethod
    def _known_variables(cls, value):
        return _check_template(value)

    missing_field: Literal["ask", "placeholder", "skip"]
    placeholder: str


class Bedetheque(_Section):
    delay: tuple[float, float]
    sitemap_delay: tuple[float, float]
    pages_dir: str
    fetcher: Optional[str] = None
    fetcher_endpoint: list[str] = Field(default_factory=list)  # one address, or several (spread out, with fallback)
    parallel: Optional[int] = Field(default=None, ge=1, le=16)  # pages fetched at once; empty: one per endpoint

    @field_validator("fetcher_endpoint", mode="before")
    @classmethod
    def _one_or_many_endpoints(cls, value):
        """``fetcher_endpoint`` may be one address or a list; nothing at all is an empty list."""
        if value is None:
            return []
        return [value] if isinstance(value, str) else value

    @field_validator("fetcher_endpoint")
    @classmethod
    def _endpoints_are_addresses(cls, value):
        for endpoint in value:
            if not endpoint.strip():
                raise ValueError("an endpoint can not be empty")
        if len(set(value)) != len(value):
            raise ValueError("an endpoint is listed twice")
        return value

    @field_validator("fetcher")
    @classmethod
    def _fetcher_exists(cls, value):
        """``package.module:function`` must lead to a function that can be imported and called."""
        if value:
            from bdnex.importer.bedetheque.fetchers import FetcherError, load_fetcher

            try:
                load_fetcher(value)
            except (FetcherError, TypeError) as err:
                raise ValueError(str(err)) from None
        return value


class Settings(_Section):
    config_version: int = Field(ge=0)
    collection_path: str
    library: str
    match: Match
    # "import" is a Python keyword
    import_: Import = Field(alias="import")
    duplicates: Duplicates
    trash: Trash
    watch: Watch
    cover: Cover
    api: Api
    write: Write
    paths: Paths
    source: str = Field(min_length=1)
    plugins: dict[str, dict[str, Any]] = Field(default_factory=dict)
    bedetheque: Bedetheque

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)

    @field_validator("import_")
    @classmethod
    def _sources_apart_from_the_collection(cls, value, info):
        """The collection and the import sources must be separate folders: a source that is (or contains, or sits in)
        the collection would make the collection's own files look like files still to be imported."""
        collection = info.data.get("collection_path")
        if not value.sources:
            return value
        folders = [(source.alias, source.folder) for source in value.sources]
        if collection and collection != PLACEHOLDER_COLLECTION:
            root = os.path.abspath(os.path.expanduser(collection)).rstrip("/")
            for alias, folder in folders:
                if folder == root:
                    raise ValueError(f"source {alias!r} is your collection_path ({root}): the collection can not be "
                                     "one of its own sources")
                if root.startswith(folder + os.sep):
                    raise ValueError(f"your collection_path ({root}) is inside the source {alias!r} ({folder}): "
                                     "sources and the collection must be separate folders")
                if folder.startswith(root + os.sep):
                    raise ValueError(f"the source {alias!r} ({folder}) is inside your collection_path ({root}): "
                                     "a source is a folder outside the collection")
        for i, (alias, folder) in enumerate(folders):
            for other_alias, other in folders[i + 1:]:
                if folder.startswith(other + os.sep) or other.startswith(folder + os.sep):
                    raise ValueError(f"the sources {alias!r} and {other_alias!r} are nested ({folder}, {other}): "
                                     "give each its own folder")
        return value

    @property
    def library_path(self) -> Path:
        return Path(os.path.expanduser(self.library))

    @property
    def cover_path(self) -> Path:
        """Where album covers are kept as files (``cover.dir``)."""
        return Path(os.path.expanduser(self.cover.dir))

    @property
    def pages_dir(self) -> Path:
        return Path(os.path.expanduser(self.bedetheque.pages_dir))

    @property
    def trash_path(self) -> Path:
        """Where replaced files go: ``trash.dir`` in the library folder (beside the database without one)."""
        if self.collection_path and self.collection_path != PLACEHOLDER_COLLECTION:
            return Path(os.path.expanduser(self.collection_path)) / self.trash.dir
        return self.library_path.parent / "trash"

    def source_for(self, path: str) -> Optional["Source"]:
        """The configured source a path is under (``None``: it is not under one)."""
        absolute = os.path.abspath(os.path.expanduser(str(path)))
        best = None
        for source in self.import_.sources:
            if absolute == source.folder or absolute.startswith(source.folder + os.sep):
                if best is None or len(source.folder) > len(best.folder):
                    best = source
        return best

    def require_collection(self) -> Path:
        """The managed library root; refuses the untouched placeholder."""
        if not self.collection_path or self.collection_path == PLACEHOLDER_COLLECTION:
            raise ConfigError(
                "`collection_path` is not set in your bdnex config: edit it "
                "(`bdnex config --edit`) and set it to your comics folder.")
        return Path(os.path.expanduser(self.collection_path))


@dataclass
class LoadedConfig:
    settings: Settings
    path: Path
    first_run: bool = False
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # merged dict, for legacy callers
    new_options: list[str] = field(default_factory=list)  # options the file does not list yet (see `bdnex config --update`)


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
    # a section whose options are all commented out is an empty YAML value, not a mistake
    user = {k: v for k, v in user.items() if not (v is None and isinstance(defaults.get(k), dict))}
    warnings: list[str] = []
    errors: list[str] = []
    for old, new in RENAMED_KEYS.items():  # an old name keeps working, once, with a warning
        if old in user:
            value = user.pop(old)
            if new in user:
                warnings.append(f"`{old}` is the old name of `{new}`, which is also set: `{old}` is ignored. Delete it.")
            else:
                user[new] = value
                warnings.append(f"`{old}` is now called `{new}`: rename it in your config "
                                "(`bdnex config --update` does it for you).")

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
            for key in unknown:
                removed_in = REMOVED_KEYS.get(key.split(".")[0])
                errors.append(f"{key}: no longer used since config_version {removed_in}: delete it"
                              if removed_in else f"{key}: unknown option (typo?)")
    if stale:
        ignored = " Delete the ignored keys listed below, then" if unknown else ""
        warnings.insert(0, (
            f"your config is stale (config_version {version}, current {CONFIG_VERSION}).{ignored} "
            f"set `config_version: {CONFIG_VERSION}` once you have checked it against "
            "`bdnex config --defaults`"))

    merged = deep_merge(defaults, user)
    merged["config_version"] = version
    try:
        settings = Settings.model_validate(merged)
        if settings.collection_path and settings.collection_path != PLACEHOLDER_COLLECTION \
                and not os.path.isdir(os.path.expanduser(settings.collection_path)):
            warnings.append(f"`collection_path` ({settings.collection_path}) is not an existing folder: create it, or fix it "
                            "with `bdnex config --edit` (a disk that is not mounted?)")
        if settings.bedetheque.fetcher_endpoint and not settings.bedetheque.fetcher:
            warnings.append(
                "bedetheque.fetcher_endpoint is set but bedetheque.fetcher is not, so nothing uses it: "
                f"album pages are read from {settings.bedetheque.pages_dir}. Set bedetheque.fetcher to the "
                "'package.module:function' that should fetch pages.")
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
    from bdnex.config_template import complete

    text, _ = complete(USER_TEMPLATE.format(version=CONFIG_VERSION, placeholder=PLACEHOLDER_COLLECTION))
    path.write_text(text, encoding="utf-8")
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
    from bdnex.config_template import missing

    try:
        new_options = missing(path.read_text(encoding="utf-8"))
    except Exception:
        new_options = []
    return LoadedConfig(settings=settings, path=path, first_run=created, warnings=warnings, raw=merged,
                        new_options=new_options)


FIRST_RUN_MESSAGE = (
    "Created a new bdnex configuration file: {path}\n"
    "Please edit it before importing: at least set `collection_path` to your comics folder\n"
    "(`bdnex config --edit` opens it, `bdnex config --defaults` lists every option).")
