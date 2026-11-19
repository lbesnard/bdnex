"""Aliases (`aliases: ["fix = review --auto"]`) and the renamed `recheck`."""

import pytest
import yaml

from bdnex import ui
from bdnex.config import ConfigError, load_config
from bdnex.ui.commands import expand_alias


def config(tmp_path, monkeypatch, aliases):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"), "aliases": aliases}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))


def test_an_alias_is_a_command_with_its_options_and_what_follows_is_added():
    assert expand_alias(["fix", "-n", "series:nains"], ["fix = review --auto"]) == ["review", "--auto", "-n", "series:nains"]
    assert expand_alias(["ls"], ["fix = review --auto"]) == ["ls"]
    assert expand_alias(["new"], ['new = import --database-only "my folder"']) == ["import", "--database-only", "my folder"]


@pytest.mark.parametrize("bad", ["fix", "Fix = review", "ls = review", "recheck = review", "x = nonsense --a",
                                 "x = ", "y = fix --auto"])
def test_a_bad_alias_is_refused_with_the_reason(tmp_path, monkeypatch, bad):
    config(tmp_path, monkeypatch, [bad, "fix = review --auto"] if bad.startswith("y") else [bad])
    with pytest.raises(ConfigError):
        load_config()


def test_the_same_name_twice_is_refused(tmp_path, monkeypatch):
    config(tmp_path, monkeypatch, ["fix = review --auto", "fix = ls"])
    with pytest.raises(ConfigError, match="twice"):
        load_config()


def test_main_runs_the_alias_and_the_old_name_says_what_it_became(tmp_path, monkeypatch, capsys):
    config(tmp_path, monkeypatch, ["look = ls --help"])
    with pytest.raises(SystemExit):
        ui._main(["look"])
    assert "usage: bdnex ls" in capsys.readouterr().out
    called = []
    monkeypatch.setattr(ui, "runner", lambda command: lambda argv: called.append((command.name, argv)) or 0)
    with pytest.raises(SystemExit):
        ui._main(["recheck", "-n"])
    assert called == [("review", ["--auto", "-n"])] and "now `bdnex review --auto`" in capsys.readouterr().err
