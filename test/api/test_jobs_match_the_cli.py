"""Every task the web page can start must build a command line the real command accepts.

The tasks are a fixed list (`jobs.SPECS`) that builds `argv` for the CLI functions. A flag renamed or removed in a command
would break its task silently (the tests of the page use fake tasks), so each flag a task can build is checked against
the command's own `--help`, with every option turned on.
"""

import re

import pytest

from bdnex import ui
from bdnex.api import jobs
from bdnex.config import build_settings

COMMAND_OF = {"check-fix": "check", "record": "import", "watch": "import", "trash-empty": "trash", "trash-restore": "trash"}

EVERYTHING = {
    "preview": True, "interactive": False, "query": ["series:nains"], "steps": ["match", "fetch"], "auto": "strong",
    "unattended": True, "sources": True, "skipped": True, "files": "database_only", "force": True,
    "formats": ["pdf", "cbr"], "copy": True, "older_than": 30, "id": 3, "place": True, "batch": "last",
    "old": "/old", "new": "/new",
}


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "collection").mkdir()
    return build_settings({"config_version": 2, "collection_path": str(tmp_path / "collection"),
                           "import": {"sources": [{"path": str(tmp_path / "src"), "mode": "copy", "name": "src"}]}})[0]


def flags_of(argv):
    return [a.split("=")[0] for a in argv if re.match(r"--[a-z]", a)]


def help_of(command, capsys):
    with pytest.raises(SystemExit):
        ui.main([command, "--help"])
    return capsys.readouterr().out


@pytest.mark.parametrize("name", sorted(jobs.SPECS))
@pytest.mark.parametrize("variant", ["preview", "real", "ask"])
def test_every_flag_a_task_builds_is_one_its_command_has(name, variant, settings, capsys, tmp_path):
    options = dict(EVERYTHING)
    options["paths"] = [str(tmp_path / "collection")]
    if variant == "real":
        options["preview"] = False
    if variant == "ask":
        options.update(preview=False, interactive=True, unattended=False)
    if name == "import" and variant != "preview":
        options["files"] = "no_move"
    try:
        _, argv = jobs.SPECS[name].build(options, settings)
    except jobs.JobError:
        pytest.skip("this combination is refused by the task itself")
    known = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", help_of(COMMAND_OF.get(name, name), capsys)))
    assert [f for f in flags_of(argv) if f not in known] == [], f"{name}: {argv}"
