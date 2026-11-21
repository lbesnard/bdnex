"""The documentation must say what the code does: these checks fail when one of them drifts.

If one fails, either document the new thing or remove what no longer exists.
"""

import glob
import re

import pytest
import yaml

from bdnex import ui
from bdnex.config import load_defaults
from bdnex.library import Library
from bdnex.library.query import ALL_FIELDS

COMMANDS = "init import ls write move repair convert aliases edit relocate check duplicates undo review reset trash series config completion serve komga candidates stats mark update".split()
ROOT = __file__.rsplit("/test/", 1)[0]


def read(path):
    with open(f"{ROOT}/{path}", encoding="utf-8") as handle:
        return handle.read()


def all_docs():
    # the generated reference is left out: it lists every option, so it would make the guides look complete
    pages = [p for p in glob.glob(f"{ROOT}/docs/**/*.md", recursive=True) if not p.endswith("reference/commands.md")]
    pages += [f"{ROOT}/README.md"]
    return "\n".join(open(page, encoding="utf-8").read() for page in pages)


def dotted(mapping, prefix=()):
    keys = set()
    for key, value in mapping.items():
        keys.add(".".join(prefix + (key,)))
        if isinstance(value, dict) and key != "plugins":  # `plugins` is free-form
            keys |= dotted(value, prefix + (key,))
    return keys


@pytest.fixture
def helps(capsys):
    texts = {}
    for command in COMMANDS:
        with pytest.raises(SystemExit):
            ui.main([command, "--help"])
        texts[command] = capsys.readouterr().out
    return texts


def test_every_option_is_in_the_configuration_reference_and_none_is_invented():
    real = dotted(load_defaults())
    documented = set()
    for block in re.findall(r"```yaml\n(.*?)```", read("docs/guides/configuration.md"), re.S):
        documented |= dotted(yaml.safe_load(block) or {})
    assert sorted(real - documented) == [], "options missing from docs/guides/configuration.md"
    assert sorted(documented - real) == [], "options documented there that do not exist"


def test_every_query_field_is_in_the_query_guide():
    guide = read("docs/guides/queries.md")
    section = guide[guide.index("## Fields"):guide.index("## Output")]
    assert [f for f in ALL_FIELDS if not re.search(rf"`{f}`", section)] == []


def test_every_table_is_in_the_library_reference():
    reference = read("docs/dev/library.md")
    tables = [r[0] for r in Library(":memory:").conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    assert [t for t in tables if f"`{t}`" not in reference] == []


def test_the_usage_text_lists_every_command(capsys):
    with pytest.raises(SystemExit):
        ui.main(["--help"])
    usage = capsys.readouterr().out
    assert [c for c in COMMANDS if not re.search(rf"^  {c}\s", usage, re.M)] == []


def test_every_flag_the_docs_mention_exists(helps):
    known = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", "\n".join(helps.values())))
    foreign = {"--extra", "--cov", "--help", "--no-project", "--python", "--build-arg", "--build"}  # other tools' flags
    mentioned = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", all_docs()))
    assert sorted(mentioned - known - foreign) == []


@pytest.mark.parametrize("command", COMMANDS)
def test_every_flag_of_a_command_is_documented(helps, command):
    docs = all_docs()
    # `--verbose` and `--yes` are the long forms of `-v` and `-y`, which the docs use
    flags = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", helps[command])) - {"--help", "--verbose", "--yes"}
    assert sorted(f for f in flags if f not in docs) == []


def test_every_command_has_a_page_or_a_section():
    docs = all_docs()
    assert [c for c in COMMANDS if f"bdnex {c}" not in docs] == []


def test_the_command_reference_is_the_current_help_of_every_command():
    from bdnex.ui import cli_reference

    def squash(text):
        # argparse differs between Python versions in its line breaks, and 3.13 writes `-s, --select TERM` for `-s TERM, --select TERM`
        return re.sub(r"(-\w) (\S+), (--[\w-]+) \2", r"\1, \3 \2", " ".join(text.split()))
    assert squash(read("docs/reference/commands.md")) == squash(cli_reference.build()), \
        "run: uv run python -m bdnex.ui.cli_reference > docs/reference/commands.md"
    assert [c for c in COMMANDS if f"### `bdnex {c}`" not in read("docs/reference/commands.md")] == []


def test_every_api_route_is_in_the_api_guide():
    pytest.importorskip("fastapi")
    from bdnex.api.app import create_app
    from bdnex.config import build_settings

    app = create_app(build_settings({"config_version": 2})[0])
    routes = {route.path for route in app.routes if route.path.startswith("/api/")}
    guide = read("docs/guides/api.md")
    undocumented = sorted(path for path in routes - {"/api/login", "/api/logout"}
                          if re.sub(r"\{[^}]*\}", "{id}", path) not in re.sub(r"\{[^}]*\}", "{id}", guide))
    assert undocumented == [], "routes missing from docs/guides/api.md"


def test_every_task_the_page_can_start_is_in_the_api_guide():
    pytest.importorskip("fastapi")
    from bdnex.api.jobs import SPECS

    guide = read("docs/guides/api.md")
    # tasks are named in prose there (the commands); each name or its command must appear
    missing = [name for name in SPECS if name not in guide and name.split("-")[0] not in guide]
    assert missing == []
