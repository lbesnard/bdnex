import os
import sys
import types

import pytest
import yaml

from bdnex import config as cfg
from bdnex.config import ConfigError, build_settings, load_config
from bdnex.importer.bedetheque import DirectoryFetcher, build_fetcher
from bdnex.ui import check_config
from bdnex.ui import config_cmd

import helpers


@pytest.fixture
def conf_path(tmp_path, monkeypatch):
    path = tmp_path / "cfg" / "bdnex.yaml"
    monkeypatch.setenv("BDNEX_CONFIG", str(path))
    return path


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)


def test_packaged_defaults_are_valid():
    settings, warnings, _ = build_settings({})
    assert warnings and "stale" in warnings[0]  # no config_version => stale
    settings, warnings, _ = build_settings({"config_version": cfg.CONFIG_VERSION})
    assert warnings == []
    assert settings.import_.mode == "in_place" and settings.match.strong_threshold == 90


def test_first_run_writes_commented_template_with_placeholder(conf_path):
    loaded = load_config()
    assert loaded.first_run and loaded.path == conf_path
    text = conf_path.read_text()
    assert "REQUIRED" in text and cfg.PLACEHOLDER_DIRECTORY in text
    assert loaded.settings.directory == cfg.PLACEHOLDER_DIRECTORY
    assert load_config().first_run is False  # second run: file exists


def test_first_run_template_is_not_a_full_copy_of_defaults(conf_path):
    load_config()
    assert yaml.safe_load(conf_path.read_text()).keys() == {"config_version", "directory"}


def test_library_directory_placeholder_is_refused(conf_path):
    settings = load_config().settings
    with pytest.raises(ConfigError, match="directory"):
        settings.require_library_directory()
    write(conf_path, {"config_version": 1, "directory": "~/comics"})
    assert str(load_config().settings.require_library_directory()) == os.path.expanduser("~/comics")


def test_user_overrides_are_deep_merged(conf_path):
    write(conf_path, {"config_version": 1, "import": {"mode": "copy"}, "match": {"use_cover": False}})
    s = load_config().settings
    assert s.import_.mode == "copy" and s.import_.autotag is True  # sibling default kept
    assert s.match.use_cover is False and s.match.strong_threshold == 90


def test_unknown_key_is_an_error_naming_the_key(conf_path):
    write(conf_path, {"config_version": 1, "import": {"mod": "copy"}, "bogus": 1})
    with pytest.raises(ConfigError) as err:
        load_config()
    assert "import.mod" in str(err.value) and "bogus" in str(err.value)
    assert str(err.value).count("bogus") == 1  # not reported twice


@pytest.mark.parametrize("data, key", [
    ({"match": {"strong_threshold": 150}}, "match.strong_threshold"),
    ({"import": {"mode": "teleport"}}, "import.mode"),
    ({"duplicates": {"keys": []}}, "duplicates.keys"),
    ({"bedetheque": {"delay": [1]}}, "bedetheque.delay"),
    ({"write": {"format": "cbr"}}, "write.format"),
])
def test_invalid_values_report_key(conf_path, data, key):
    write(conf_path, {"config_version": 1, **data})
    with pytest.raises(ConfigError, match=key):
        load_config()


def test_all_problems_reported_together(conf_path):
    write(conf_path, {"config_version": 1, "typo": 1, "match": {"use_cover": "maybe"}})
    with pytest.raises(ConfigError) as err:
        load_config()
    assert "typo" in str(err.value) and "match.use_cover" in str(err.value)


def test_stale_config_warns_and_ignores_old_keys(conf_path):
    # shape of the files written by bdnex 0.1
    write(conf_path, {"bdnex": {"config_path": "~/.config/bdnex", "share_path": "~/x"},
                      "directory": "~/comics", "import": {"copy": False, "move": True, "rename": True},
                      "paths": {"series": "%title"}})
    loaded = load_config()
    assert any("stale" in w for w in loaded.warnings)
    assert any("import.copy" in w and "import.move" in w for w in loaded.warnings)
    assert loaded.settings.import_.mode == "in_place"  # old keys ignored, default used
    assert loaded.settings.paths.series == "%title"  # known keys still honoured


def test_stale_config_still_rejects_bad_values(conf_path):
    write(conf_path, {"match": {"strong_threshold": -5}})
    with pytest.raises(ConfigError, match="match.strong_threshold"):
        load_config()


def test_config_version_must_be_int(conf_path):
    write(conf_path, {"config_version": "one"})
    with pytest.raises(ConfigError, match="config_version"):
        load_config()


def test_invalid_yaml_and_non_mapping(conf_path):
    write(conf_path, "key: [unclosed")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config()
    write(conf_path, "- a\n- b\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_config()


def test_env_and_xdg_path_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv("BDNEX_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cfg.user_config_path() == tmp_path / "bdnex" / "bdnex.yaml"
    monkeypatch.setenv("BDNEX_CONFIG", str(tmp_path / "x.yaml"))
    assert cfg.user_config_path() == tmp_path / "x.yaml"


def test_derived_paths_expand_user(conf_path):
    s = load_config().settings
    assert s.library_path == s.library_path.expanduser() and "~" not in str(s.share_dir)
    assert "~" not in str(s.pages_dir)


# ----------------------------------------------------------------- fetcher option
def test_default_fetcher_is_directory_of_saved_pages(conf_path):
    assert isinstance(build_fetcher(load_config().settings), DirectoryFetcher)


def test_fetcher_spec_resolves_to_the_function(conf_path):
    write(conf_path, {"config_version": 1, "bedetheque": {"fetcher": "helpers:fake_fetch"}})
    fetch = build_fetcher(load_config().settings)
    assert fetch is helpers.fake_fetch
    assert fetch("https://x/BD-a-1.html") == b"<html>https://x/BD-a-1.html</html>"


def test_fetcher_endpoint_is_optional_and_readable(conf_path):
    assert load_config().settings.bedetheque.fetcher_endpoint is None
    write(conf_path, {"config_version": 1, "bedetheque": {"fetcher_endpoint": "http://localhost:8191"}})
    assert load_config().settings.bedetheque.fetcher_endpoint == "http://localhost:8191"


def test_fetcher_endpoint_must_be_a_string(conf_path):
    write(conf_path, {"config_version": 1, "bedetheque": {"fetcher_endpoint": 8191}})
    with pytest.raises(ConfigError, match="fetcher_endpoint"):
        load_config()


def test_fetcher_spec_must_be_callable(conf_path, monkeypatch):
    module = types.ModuleType("not_callable_mod")
    module.value = 3
    monkeypatch.setitem(sys.modules, "not_callable_mod", module)
    write(conf_path, {"config_version": 1, "bedetheque": {"fetcher": "not_callable_mod:value"}})
    with pytest.raises(TypeError):
        build_fetcher(load_config().settings)


def test_plugin_runs_with_configured_fetcher(conf_path):
    from bdnex.importer.bedetheque import Bedetheque
    from bdnex.library import Library
    write(conf_path, {"config_version": 1, "bedetheque": {
        "fetcher": "helpers:fake_fetch"}})
    with Library(":memory:") as lib:
        plugin = Bedetheque(lib, fetch=build_fetcher(load_config().settings), sleep=lambda s: None)
        assert plugin._get("album", "https://x/BD-a-1.html").startswith(b"<html>https://x/BD-a-1.html")
        assert lib.cache_get("bedetheque", "album", "https://x/BD-a-1.html") is not None


# ------------------------------------------------------------------------ CLI
def test_first_launch_tells_user_to_edit_config(conf_path, capsys):
    assert check_config() is True
    err = capsys.readouterr().err
    assert str(conf_path) in err and "edit" in err and "directory" in err
    assert check_config() is True and capsys.readouterr().err == ""  # only once


def test_check_config_blocks_on_invalid_config(conf_path, capsys):
    write(conf_path, {"config_version": 1, "oops": 1})
    assert check_config() is False
    assert "oops" in capsys.readouterr().err


def test_check_config_prints_stale_warning(conf_path, capsys):
    write(conf_path, {"directory": "~/c"})
    assert check_config() is True
    assert "stale" in capsys.readouterr().err


def test_config_command_path_show_defaults(conf_path, capsys):
    assert config_cmd.run(["--path"]) == 0
    assert capsys.readouterr().out.strip() == str(conf_path)
    assert config_cmd.run([]) == 0
    assert "match:" in capsys.readouterr().out
    assert config_cmd.run(["--defaults"]) == 0
    assert "strong_threshold" in capsys.readouterr().out


def test_config_path_works_even_when_file_is_invalid(conf_path, capsys):
    write(conf_path, {"config_version": 1, "oops": 1})
    assert config_cmd.run(["-p"]) == 0
    assert capsys.readouterr().out.strip() == str(conf_path)


def test_config_command_reports_invalid_config(conf_path, capsys):
    write(conf_path, {"config_version": 1, "oops": 1})
    assert config_cmd.run([]) == 1
    assert "oops" in capsys.readouterr().err


def test_config_edit_uses_editor(conf_path, monkeypatch):
    calls = []
    monkeypatch.setenv("EDITOR", "myeditor --flag")
    monkeypatch.setattr(config_cmd.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert config_cmd.run(["--edit"]) == 0
    assert calls == [["myeditor", "--flag", str(conf_path)]]


# --------------------------------------------------------------------- legacy
def test_legacy_bdnex_config_merges_defaults(conf_path):
    from bdnex.lib.utils import bdnex_config
    write(conf_path, {"directory": "~/c"})  # old-style file lacking most keys
    conf = bdnex_config()
    assert conf["directory"] == "~/c"
    assert conf["bdnex"]["share_path"] and conf["cover"]["match_percentage"] == 40
