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
    assert "REQUIRED" in text and cfg.PLACEHOLDER_COLLECTION in text
    assert loaded.settings.collection_path == cfg.PLACEHOLDER_COLLECTION
    assert load_config().first_run is False  # second run: file exists


def test_first_run_template_is_not_a_full_copy_of_defaults(conf_path):
    load_config()
    assert yaml.safe_load(conf_path.read_text()).keys() == {"config_version", "collection_path"}


def test_library_directory_placeholder_is_refused(conf_path):
    settings = load_config().settings
    with pytest.raises(ConfigError, match="collection_path"):
        settings.require_collection()
    write(conf_path, {"config_version": 2, "collection_path": "~/comics"})
    assert str(load_config().settings.require_collection()) == os.path.expanduser("~/comics")


def test_user_overrides_are_deep_merged(conf_path):
    write(conf_path, {"config_version": 2, "import": {"mode": "copy"}, "match": {"medium_threshold": 60}})
    s = load_config().settings
    assert s.import_.mode == "copy" and s.import_.resume is True  # sibling default kept
    assert s.match.medium_threshold == 60 and s.match.strong_threshold == 90


def test_unknown_key_is_an_error_naming_the_key(conf_path):
    write(conf_path, {"config_version": 2, "import": {"mod": "copy"}, "bogus": 1})
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
    write(conf_path, {"config_version": 2, **data})
    with pytest.raises(ConfigError, match=key):
        load_config()


def test_all_problems_reported_together(conf_path):
    write(conf_path, {"config_version": 2, "typo": 1, "match": {"strong_threshold": "high"}})
    with pytest.raises(ConfigError) as err:
        load_config()
    assert "typo" in str(err.value) and "match.strong_threshold" in str(err.value)


def test_stale_config_warns_and_ignores_old_keys(conf_path):
    # shape of the files written by bdnex 0.1
    write(conf_path, {"bdnex": {"config_path": "~/.config/bdnex", "share_path": "~/x"},
                      "collection_path": "~/comics", "import": {"copy": False, "move": True, "rename": True},
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
    assert s.library_path == s.library_path.expanduser()
    assert "~" not in str(s.pages_dir)


# ----------------------------------------------------------------- fetcher option
def test_default_fetcher_is_directory_of_saved_pages(conf_path):
    assert isinstance(build_fetcher(load_config().settings), DirectoryFetcher)


def test_fetcher_spec_resolves_to_the_function(conf_path):
    write(conf_path, {"config_version": 2, "bedetheque": {"fetcher": "helpers:fake_fetch"}})
    fetch = build_fetcher(load_config().settings)
    assert fetch is helpers.fake_fetch
    assert fetch("https://x/BD-a-1.html") == b"<html>https://x/BD-a-1.html</html>"


def test_fetcher_endpoint_is_optional_and_readable(conf_path):
    assert load_config().settings.bedetheque.fetcher_endpoint == []
    write(conf_path, {"config_version": 2, "bedetheque": {"fetcher_endpoint": "http://localhost:8191"}})
    assert load_config().settings.bedetheque.fetcher_endpoint == ["http://localhost:8191"]


def test_fetcher_endpoint_must_be_a_string(conf_path):
    write(conf_path, {"config_version": 2, "bedetheque": {"fetcher_endpoint": 8191}})
    with pytest.raises(ConfigError, match="fetcher_endpoint"):
        load_config()


def test_fetcher_spec_must_be_callable(conf_path, monkeypatch):
    module = types.ModuleType("not_callable_mod")
    module.value = 3
    monkeypatch.setitem(sys.modules, "not_callable_mod", module)
    write(conf_path, {"config_version": 2, "bedetheque": {"fetcher": "not_callable_mod:value"}})
    with pytest.raises(ConfigError, match="bedetheque.fetcher: .*not callable"):  # caught when the config is read
        load_config()


def test_plugin_runs_with_configured_fetcher(conf_path):
    from bdnex.importer.bedetheque import Bedetheque
    from bdnex.library import Library
    write(conf_path, {"config_version": 2, "bedetheque": {
        "fetcher": "helpers:fake_fetch"}})
    with Library(":memory:") as lib:
        plugin = Bedetheque(lib, fetch=build_fetcher(load_config().settings), sleep=lambda s: None)
        assert plugin._get("album", "https://x/BD-a-1.html").startswith(b"<html>https://x/BD-a-1.html")
        assert lib.cache_get("bedetheque", "album", "https://x/BD-a-1.html") is not None


# ------------------------------------------------------------------------ CLI
def test_first_launch_tells_user_to_edit_config(conf_path, capsys):
    assert check_config() is True
    err = capsys.readouterr().err
    assert str(conf_path) in err and "edit" in err and "collection_path" in err
    assert check_config() is True and capsys.readouterr().err == ""  # only once


def test_check_config_blocks_on_invalid_config(conf_path, capsys):
    write(conf_path, {"config_version": 2, "oops": 1})
    assert check_config() is False
    assert "oops" in capsys.readouterr().err


def test_check_config_prints_stale_warning(conf_path, capsys):
    write(conf_path, {"collection_path": "~/c"})
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
    write(conf_path, {"config_version": 2, "oops": 1})
    assert config_cmd.run(["-p"]) == 0
    assert capsys.readouterr().out.strip() == str(conf_path)


def test_config_command_reports_invalid_config(conf_path, capsys):
    write(conf_path, {"config_version": 2, "oops": 1})
    assert config_cmd.run([]) == 1
    assert "oops" in capsys.readouterr().err


def test_config_edit_uses_editor(conf_path, monkeypatch):
    calls = []
    monkeypatch.setenv("EDITOR", "myeditor --flag")
    monkeypatch.setattr(config_cmd.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert config_cmd.run(["--edit"]) == 0
    assert calls == [["myeditor", "--flag", str(conf_path)]]


def test_options_that_did_nothing_are_gone(conf_path):
    for removed in ({"match": {"use_cover": True}}, {"import": {"autotag": True}}, {"paths": {"default": "x"}}, {"write": {"format": "cbz"}}):
        write(conf_path, {"config_version": 2, **removed})
        with pytest.raises(ConfigError, match="unknown option"):
            load_config()


def test_plugins_section_must_be_a_mapping(conf_path):
    write(conf_path, {"config_version": 2, "plugins": ["x"]})
    with pytest.raises(ConfigError, match="plugins"):
        load_config()


def test_version_1_config_with_the_old_paths_block_is_stale_not_broken(conf_path):
    """A full v1 config (as written by `bdnex config`) keeps working after the `bdnex:` block was removed."""
    write(conf_path, {"config_version": 1, "bdnex": {"config_path": "~/.config/bdnex", "share_path": "~/.local/share/bdnex"},
                      "collection_path": "~/comics", "match": {"strong_threshold": 85}})
    loaded = load_config()
    assert loaded.settings.match.strong_threshold == 85 and loaded.settings.collection_path == "~/comics"
    assert any("stale" in w for w in loaded.warnings)
    assert any("ignored keys no longer used: bdnex" in w for w in loaded.warnings)


def test_a_removed_option_at_the_current_version_says_to_delete_it(conf_path):
    write(conf_path, {"config_version": 2, "bdnex": {"config_path": "x", "share_path": "y"}})
    with pytest.raises(ConfigError) as err:
        load_config()
    message = str(err.value)
    assert "bdnex: no longer used since config_version 2: delete it" in message
    assert "typo" not in message and message.count("bdnex:") == 1  # said once, not once per sub-key


def test_a_real_typo_still_says_typo(conf_path):
    write(conf_path, {"config_version": 2, "imprt": {}})
    with pytest.raises(ConfigError, match="imprt: unknown option \\(typo\\?\\)"):
        load_config()


def test_stale_warning_tells_you_to_delete_the_ignored_keys_first(conf_path, tmp_path):
    os.makedirs(os.path.expanduser("~/c"))
    write(conf_path, {"config_version": 1, "bdnex": {"share_path": "y"}, "collection_path": "~/c"})
    first, second = load_config().warnings
    assert "Delete the ignored keys" in first and "set `config_version: 2`" in first
    assert second == "ignored keys no longer used: bdnex"


def test_endpoint_without_a_fetcher_is_called_out(conf_path):
    write(conf_path, {"config_version": 2, "bedetheque": {"fetcher": None, "fetcher_endpoint": "http://localhost:1"}})
    warnings = load_config().warnings
    assert len(warnings) == 1 and "fetcher_endpoint is set but bedetheque.fetcher is not" in warnings[0]


def test_no_warning_when_the_fetcher_is_set_or_nothing_is(conf_path):
    write(conf_path, {"config_version": 2, "bedetheque": {"fetcher": "helpers:fake_fetch", "fetcher_endpoint": "x"}})
    assert load_config().warnings == []
    write(conf_path, {"config_version": 2})
    assert load_config().warnings == []


@pytest.mark.parametrize("written, expected", [("no", "no"), ("yes", "yes"), ("ask", "ask")])
def test_an_unquoted_yes_or_no_in_the_yaml_file_is_accepted(tmp_path, written, expected):
    """YAML itself reads ``delete_original: no`` as false."""
    import yaml
    user = yaml.safe_load(f"config_version: 2\ncollection_path: {tmp_path}\nwrite:\n  delete_original: {written}\n")
    assert build_settings(user)[0].write.delete_original == expected
    assert build_settings(user)[0].write.delete_original in ("ask", "yes", "no")


def test_other_wrong_values_are_still_reported(tmp_path):
    with pytest.raises(ConfigError, match="delete_original"):
        build_settings({"config_version": 2, "collection_path": str(tmp_path), "write": {"delete_original": "maybe"}})


def test_the_fetcher_must_exist():
    import pytest
    from bdnex.config import ConfigError
    for bad, message in (("no_colon", "package.module:function"), ("no.such.module:fn", "can not import"),
                         ("bdnex.importer.bedetheque:nothing_here", "no function"), ("os:sep", "not callable")):
        with pytest.raises(ConfigError, match=message):
            build_settings({"config_version": 2, "bedetheque": {"fetcher": bad}})
    settings, warnings, _ = build_settings({"config_version": 2, "bedetheque": {"fetcher": "os.path:exists"}})
    assert settings.bedetheque.fetcher == "os.path:exists" and warnings == []
    assert build_settings({"config_version": 2, "bedetheque": {"fetcher": None}})[0].bedetheque.fetcher is None


def test_flaresolverr_is_the_default_fetcher_and_needs_an_endpoint_to_be_used():
    from bdnex.importer.bedetheque.fetchers import DEFAULT_FETCHER, uses_saved_pages
    settings = build_settings({"config_version": 2})[0]
    assert settings.bedetheque.fetcher == DEFAULT_FETCHER and uses_saved_pages(settings)  # no address: saved pages
    with_endpoint = build_settings({"config_version": 2, "bedetheque": {"fetcher_endpoint": "http://localhost:8191/v1"}})[0]
    assert not uses_saved_pages(with_endpoint)
    own = build_settings({"config_version": 2, "bedetheque": {"fetcher": "os.path:exists"}})[0]
    assert not uses_saved_pages(own)  # a fetcher of your own is used as it is


def test_a_path_template_with_a_typo_is_refused():
    with pytest.raises(ConfigError, match=r"paths.series: .*unknown variable\(s\) \$autor"):
        build_settings({"config_version": 2, "paths": {"series": "Series/$autor/$title"}})
    with pytest.raises(ConfigError, match=r"paths.rules.0.template: .*\$serie\b"):
        build_settings({"config_version": 2, "paths": {"rules": [{"match": "series:x", "template": "A/$serie"}]}})
    build_settings({"config_version": 2, "paths": {"series": "S/${series}/$series - T$volume:02 - $title"}})  # fine


def test_a_rule_with_a_bad_query_is_refused():
    with pytest.raises(ConfigError, match=r"paths.rules.0.match: .*bad regular expression"):
        build_settings({"config_version": 2, "paths": {"rules": [{"match": ["title::("], "template": "A/$title"}]}})
    with pytest.raises(ConfigError, match=r"paths.rules.0.match: .*not a number"):
        build_settings({"config_version": 2, "paths": {"rules": [{"match": "year:abc..", "template": "A"}]}})


def test_template_variables_match_what_an_album_provides():
    from bdnex.organizer import template
    album = {"kind": "series", "series": "S", "title": "T", "credits": {}, "genres": []}
    assert tuple(template.fields_for(album)) == template.FIELD_NAMES


def test_a_value_set_later_is_checked_too():
    settings = build_settings({"config_version": 2})[0]
    with pytest.raises(Exception, match="format_preferred"):
        settings.write.format_preferred = "maybe"
    settings.write.format_preferred = "none"
    assert settings.write.format_preferred == "none"


def test_a_missing_directory_is_warned_about(tmp_path):
    _, warnings, _ = build_settings({"config_version": 2, "collection_path": str(tmp_path / "unmounted")})
    assert any("not an existing folder" in w for w in warnings)
    _, none, _ = build_settings({"config_version": 2, "collection_path": str(tmp_path)})
    assert none == []


def test_editing_the_config_checks_what_was_saved(conf_path, monkeypatch, capsys):
    from bdnex.ui import config_cmd
    write(conf_path, {"config_version": 2})
    monkeypatch.setenv("EDITOR", "true")
    assert config_cmd._edit(conf_path) == 0 and "configuration is valid" in capsys.readouterr().err
    write(conf_path, {"config_version": 2, "match": {"strong_threshold": 500}})
    assert config_cmd._edit(conf_path) == 1 and "strong_threshold" in capsys.readouterr().err


def test_the_old_name_of_the_collection_option_still_works_with_a_warning(tmp_path):
    settings, warnings, _ = build_settings({"config_version": 2, "directory": str(tmp_path)})
    assert settings.collection_path == str(tmp_path)
    assert any("`directory` is now called `collection_path`" in w for w in warnings)
    _, both, _ = build_settings({"config_version": 2, "directory": "~/a", "collection_path": str(tmp_path)})
    assert any("is also set" in w for w in both)


def test_config_update_renames_the_old_option_in_the_file(conf_path, capsys, tmp_path):
    from bdnex.ui import config_cmd
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(f"config_version: 2\ndirectory: {tmp_path}\nwrite:\n  cbr: skip\n", encoding="utf-8")
    assert config_cmd._update(conf_path) == 0
    text = conf_path.read_text(encoding="utf-8")
    assert f"collection_path: {tmp_path}" in text and "\ndirectory:" not in text and "cbr: skip" in text
    assert "Renamed the option directory -> collection_path" in capsys.readouterr().out
    assert load_config(conf_path).warnings == []


def test_fetcher_endpoint_is_one_address_or_a_list():
    one = build_settings({"config_version": 2, "bedetheque": {"fetcher_endpoint": "http://a/v1"}})[0].bedetheque
    assert one.fetcher_endpoint == ["http://a/v1"] and one.parallel is None
    many = build_settings({"config_version": 2, "bedetheque": {"fetcher_endpoint": ["http://a/v1", "http://b/v1"],
                                                               "parallel": 4}})[0].bedetheque
    assert many.fetcher_endpoint == ["http://a/v1", "http://b/v1"] and many.parallel == 4
    with pytest.raises(ConfigError, match="listed twice"):
        build_settings({"config_version": 2, "bedetheque": {"fetcher_endpoint": ["http://a/v1", "http://a/v1"]}})
    with pytest.raises(ConfigError, match="parallel"):
        build_settings({"config_version": 2, "bedetheque": {"parallel": 0}})
