"""The user's config file lists every option; what a new version adds is appended, commented out."""

import re

import pytest
import yaml

from bdnex import config as cfg
from bdnex.config import Settings, build_settings, load_config, load_defaults
from bdnex.config_template import comment_out, complete, missing, packaged

MINIMAL = "config_version: 2\ncollection_path: /comics\n"


# ------------------------------------------------- the packaged file and the model agree, exactly
def model_options():
    fields = {}
    for name, field in Settings.model_fields.items():
        key = field.alias or name
        sub = field.annotation
        fields[key] = sorted(sub.model_fields) if hasattr(sub, "model_fields") else None
    return fields


def test_the_packaged_defaults_list_exactly_the_options_of_the_model():
    listed = {item.name: [c for c, _ in item.children] or None for item in packaged()}
    listed.update({"config_version": None, "collection_path": None})
    model = model_options()
    assert set(listed) == set(model)
    for key, children in model.items():
        if children is not None:
            assert sorted(listed[key]) == children, key


def test_every_packaged_option_has_a_comment_explaining_it():
    text = cfg.DEFAULTS_PATH.read_text(encoding="utf-8")
    bare = []
    for line in text.splitlines():
        match = re.match(r"^  ([\w-]+):", line)
        if match and "#" not in line:
            bare.append(match.group(1))
    # a few are self-explanatory by name; the list is capped so new options come with a comment
    assert len(bare) <= 12, bare


# ------------------------------------------------------------------------- completing a file
def test_a_minimal_file_gets_every_option_commented_out():
    text, added = complete(MINIMAL)
    assert missing(text) == [] and len(added) > 40
    assert yaml.safe_load(text) == {"config_version": 2, "collection_path": "/comics"}  # nothing became active


def test_uncommenting_everything_gives_back_the_defaults():
    text, _ = complete(MINIMAL)
    active = "\n".join(re.sub(r"^(\s*)# ?", r"\1", line) if re.match(r"^\s*# ?[\w-]+:", line) else line
                       for line in text.splitlines())
    loaded = yaml.safe_load(active)
    defaults = load_defaults()
    for key in ("config_version", "collection_path"):
        defaults.pop(key), loaded.pop(key)
    assert loaded == defaults


def test_completing_twice_changes_nothing():
    once, _ = complete(MINIMAL)
    twice, added = complete(once)
    assert twice == once and added == []


def test_what_the_user_wrote_is_kept_and_only_missing_options_are_added():
    mine = ("config_version: 2\ncollection_path: /comics\n\n# my notes\nimport:\n  mode: copy   # copy, please\n"
            "  # workers: 3\n\nwrite:\n  delete_original: 'no'\n")
    text, added = complete(mine)
    assert text.startswith(mine.split("\nwrite:")[0])  # the top of the file is byte for byte the same
    assert "  mode: copy   # copy, please" in text and "  delete_original: 'no'" in text
    assert "import.workers" not in added and "import.mode" not in added and "write.delete_original" not in added
    assert "import.resume" in added and "write.size_tolerance" in added
    import_part = text.split("\nwrite:")[0]
    assert import_part.count("workers:") == 1  # the commented one of `import` was recognised, not repeated
    assert "write.workers" in added
    assert yaml.safe_load(text)["import"] == {"mode": "copy"}


def test_missing_options_go_inside_their_section_not_after_the_next_one():
    mine = "config_version: 2\ncollection_path: /comics\nimport:\n  mode: copy\n\nwrite:\n  cbr: skip\n"
    text, _ = complete(mine)
    lines = text.splitlines()
    first_write = lines.index("write:")
    import_part = lines[lines.index("import:"):first_write]
    assert any("# resume:" in line for line in import_part)
    assert not any("# size_tolerance:" in line for line in import_part)
    assert any("# size_tolerance:" in line for line in lines[first_write:])


def test_a_new_option_of_a_new_version_is_detected_and_added(monkeypatch):
    old, _ = complete(MINIMAL)
    original = cfg.DEFAULTS_PATH.read_text(encoding="utf-8")
    patched = original.replace("  size_change: ask ", "  brand_new: 1                          # added by a later version\n  size_change: ask ", 1)
    monkeypatch.setattr("bdnex.config_template.packaged", lambda: _parse(patched))
    assert missing(old) == ["write.brand_new"]
    text, added = complete(old)
    assert added == ["write.brand_new"] and "  # brand_new: 1" in text


def _parse(text):
    from bdnex.config_template import CHILD, TOP, MANAGED, Section
    items = []
    for raw in text.splitlines():
        top, child = TOP.match(raw), CHILD.match(raw)
        if top:
            items.append(Section(top.group(1), raw))
        elif child and items:
            items[-1].children.append((child.group(1), raw))
    return [i for i in items if i.name not in MANAGED]


def test_a_section_the_file_lacks_is_appended_commented():
    text, _ = complete(MINIMAL)
    assert "\n# api:\n" in text and "  # port: 8765" in text


# ------------------------------------------------------------- the file still loads, whatever is commented
def test_a_section_whose_options_are_all_commented_out_is_not_an_error(tmp_path):
    user = yaml.safe_load(f"config_version: 2\ncollection_path: {tmp_path}\nmatch:\n  # strong_threshold: 90\nwrite:\n")
    assert user["match"] is None and user["write"] is None
    settings, warnings, _ = build_settings(user)
    assert settings.match.strong_threshold == 90 and warnings == []


def test_the_first_run_file_lists_every_option_and_loads_with_the_defaults(tmp_path):
    loaded = load_config(tmp_path / "new.yaml")
    assert loaded.first_run and loaded.new_options == []
    text = (tmp_path / "new.yaml").read_text(encoding="utf-8")
    assert "# strong_threshold: 90" in text and text.startswith("# bdnex configuration")
    assert loaded.settings.match.strong_threshold == 90
    assert loaded.settings.collection_path == cfg.PLACEHOLDER_COLLECTION


def test_a_file_from_an_older_version_reports_what_is_new(tmp_path):
    path = tmp_path / "old.yaml"
    path.write_text("config_version: 2\ncollection_path: /comics\nwrite:\n  cbr: skip\n", encoding="utf-8")
    loaded = load_config(path)
    assert "write.size_tolerance" in loaded.new_options and "match.strong_threshold" in loaded.new_options


def test_the_update_command_adds_them_and_keeps_a_backup(tmp_path, monkeypatch, capsys):
    from bdnex.ui import config_cmd
    path = tmp_path / "bdnex.yaml"
    path.write_text("config_version: 2\ncollection_path: /comics\nwrite:\n  cbr: skip\n", encoding="utf-8")
    monkeypatch.setenv("BDNEX_CONFIG", str(path))
    assert config_cmd.run(["--update"]) == 0
    out = capsys.readouterr().out
    assert "Added" in out and "write.size_tolerance" in out
    assert (tmp_path / "bdnex.yaml.bak").read_text(encoding="utf-8").endswith("  cbr: skip\n")
    assert load_config(path).new_options == []
    assert config_cmd.run(["--update"]) == 0 and "already lists every option" in capsys.readouterr().out


def test_the_run_says_once_that_the_file_lacks_options(tmp_path, monkeypatch, capsys):
    from bdnex.ui import check_config
    path = tmp_path / "bdnex.yaml"
    path.write_text("config_version: 2\ncollection_path: /comics\n", encoding="utf-8")
    monkeypatch.setenv("BDNEX_CONFIG", str(path))
    assert check_config() is True
    assert "bdnex config --update" in capsys.readouterr().err


def test_comment_out_keeps_the_indent():
    assert comment_out("  key: 1  # help") == "  # key: 1  # help"
    assert comment_out("key: 1") == "# key: 1"


# ------------------------------------------------- the possible values come back
def test_update_puts_the_help_back_after_options_that_lost_it():
    from bdnex.config_template import annotate

    text = MINIMAL + "write:\n  cbr: convert\n  # pdf: convert\n  workers: 2  # my own note\n"
    new, done = annotate(text)
    assert "write.cbr" in done and "write.pdf" in done and "write.workers" not in done
    by_key = {m.group(1): m.group(0) for m in re.finditer(r"(?m)^  #? ?(cbr|pdf|workers):.*$", new)}
    assert "convert | skip" in by_key["cbr"] and "convert | tag | skip" in by_key["pdf"]
    assert by_key["workers"].endswith("# my own note")  # a comment of the user's is never replaced
    assert yaml.safe_load(new)["write"] == {"cbr": "convert", "workers": 2}  # values untouched
    assert annotate(new)[1] == []  # and once is enough


def test_the_help_is_not_added_to_a_value_that_contains_a_hash_or_to_an_empty_line():
    from bdnex.config_template import annotate

    text = MINIMAL + "write:\n  conflict: 'a # b'\n  cbr:\n"
    assert annotate(text)[1] == []


def test_config_update_reports_the_help_it_restored(tmp_path, monkeypatch, capsys):
    from bdnex.config_template import complete
    from bdnex.ui import config_cmd

    path = tmp_path / "c.yaml"
    full, _ = complete(MINIMAL + "write:\n  cbr: convert\n  # pdf: convert\n")
    path.write_text(full)
    monkeypatch.setattr(config_cmd, "user_config_path", lambda: path)
    assert config_cmd.run(["--update"]) == 0
    assert "Put the list of possible values back after" in capsys.readouterr().out
    assert re.search(r"(?m)^  cbr: convert\s+# convert \| skip", path.read_text())


# ------------------------------------------------- set one option, keeping the file as it is
def test_set_value_changes_an_active_line_and_keeps_its_comment():
    from bdnex.config_template import set_value

    text = MINIMAL + "write:\n  cbr: convert\n  pdf: convert   # convert | tag | skip\n  workers: 2\n"
    new = set_value(text, "write.pdf", "tag")
    assert "  pdf: tag   # convert | tag | skip\n" in new and "  cbr: convert\n" in new and "workers: 2" in new
    assert yaml.safe_load(new)["write"] == {"cbr": "convert", "pdf": "tag", "workers": 2}


def test_set_value_activates_a_commented_option_and_its_commented_section():
    from bdnex.config_template import set_value

    new = set_value(MINIMAL + "# write:\n#   cbr: convert    # convert | skip\n#   pdf: convert\n", "write.pdf", "skip")
    assert yaml.safe_load(new)["write"] == {"pdf": "skip"}
    assert "#   cbr: convert" in new and new.count("pdf:") == 1  # the others stay as they were, and pdf is not there twice
    again = set_value(MINIMAL + "write:\n  # pdf: convert  # help\n", "write.pdf", "tag")
    assert "  pdf: tag  # help" in again and yaml.safe_load(again)["write"] == {"pdf": "tag"}


def test_set_value_adds_a_missing_option_or_section_and_top_level_options():
    from bdnex.config_template import set_value

    assert yaml.safe_load(set_value(MINIMAL + "write:\n  cbr: skip\n\ncover:\n  dir: /c\n", "write.workers", 3))["write"] == {"cbr": "skip", "workers": 3}
    assert yaml.safe_load(set_value(MINIMAL, "write.pdf", "tag"))["write"] == {"pdf": "tag"}
    assert yaml.safe_load(set_value(MINIMAL + "source: bedetheque\n", "source", "other"))["source"] == "other"
    assert yaml.safe_load(set_value(MINIMAL, "library", "/data/x.sqlite"))["library"] == "/data/x.sqlite"


@pytest.mark.parametrize("value", [None, True, False, 0, 7, 1.5, "plain", "yes", "no", "12", "a: b", "has # hash", "é accents", "", " spaced "])
def test_set_value_round_trips_any_scalar(value):
    from bdnex.config_template import set_value

    assert yaml.safe_load(set_value(MINIMAL + "write:\n  conflict: ask\n", "write.conflict", value))["write"]["conflict"] == value
