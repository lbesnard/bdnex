"""`bdnex aliases`: list them, add one, remove one (the configuration file is edited)."""

import io
import json

import pytest
import yaml

from bdnex.config import load_config
from bdnex.config_template import set_aliases
from bdnex.ui import aliases_cmd

BASE = "config_version: 2\n# my notes\ncollection_path: /comics\n\nwrite:\n  pdf: skip   # keep PDFs\n"


@pytest.fixture
def conf(tmp_path, monkeypatch):
    path = tmp_path / "bdnex.yaml"
    path.write_text(BASE + f"library: {tmp_path / 'lib.sqlite'}\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(path))
    return path


def run(*argv):
    out = io.StringIO()
    return aliases_cmd.run(list(argv), out=out), out.getvalue()


def test_the_listing_shows_bdnexs_own_aliases_with_what_they_run_and_why(conf):
    code, text = run()
    assert code == 0 and "settle-doubtful-matches" in text and "bdnex review --auto" in text and "[yours" not in text
    rows = json.loads(run("--json")[1])
    assert {"name", "command", "why", "from"} <= set(rows[0]) and all(row["from"] == "bdnex" for row in rows)


def test_add_writes_a_block_keeps_the_rest_of_the_file_and_keeps_a_backup(conf):
    before = conf.read_text()
    code, text = run("--add", "show-unlinked = ls --unmatched  # files that have no album")
    assert code == 0 and "Added show-unlinked" in text and "[yours]" in text
    after = conf.read_text()
    assert after.startswith(before.rstrip("\n")) and "# my notes" in after and "pdf: skip   # keep PDFs" in after
    assert (conf.with_name(conf.name + ".bak")).read_text() == before
    names = [a.partition("=")[0].strip() for a in load_config().settings.aliases]
    assert "show-unlinked" in names and "settle-doubtful-matches" in names  # added to bdnex's, not replacing


def test_the_same_name_replaces_and_rm_takes_it_back_to_bdnexs_own(conf):
    run("--add", "settle-doubtful-matches = review --auto -y  # mine")
    assert "[yours (replaces bdnex's)]" in run()[1]
    code, text = run("--rm", "settle-doubtful-matches")
    assert code == 0 and "bdnex's own of that name is back" in text
    assert "mine" not in conf.read_text() and "[yours" not in run()[1]


def test_rm_of_one_of_bdnexs_switches_it_off_for_you(conf):
    code, text = run("--rm", "first-import")
    assert code == 0 and "Switched off first-import" in text
    assert "first-import" not in run()[1]
    assert "first-import" not in " ".join(load_config().settings.aliases)
    assert yaml.safe_load(conf.read_text())["aliases"] == ["first-import ="]


def test_bad_input_changes_nothing_and_says_why(conf):
    before = conf.read_text()
    code, _ = run("--add", "ls = review")
    assert code == 2 and conf.read_text() == before
    assert run("--add", "nonsense")[0] == 2
    code, _ = run("--rm", "no-such-alias")
    assert code == 1 and conf.read_text() == before


def test_a_preview_changes_nothing(conf):
    before = conf.read_text()
    code, text = run("-n", "--add", "show-unlinked = ls --unmatched")
    assert code == 0 and "Would add show-unlinked" in text and conf.read_text() == before
    assert not conf.with_name(conf.name + ".bak").exists()


def test_set_aliases_handles_a_flow_list_a_block_a_comment_and_no_key():
    assert set_aliases("a: 1\n", ["x = ls"]).endswith('aliases:\n  - "x = ls"\n')
    assert set_aliases("a: 1\naliases: []   # mine\nb: 2\n", ["x = ls"]) == 'a: 1\naliases:  # mine\n  - "x = ls"\nb: 2\n'
    block = 'a: 1\naliases:\n  - "x = ls"\n  # note\n  - "y = ls"\n\nb: 2\n'
    assert set_aliases(block, ["z = ls"]) == 'a: 1\naliases:\n  - "z = ls"\n\nb: 2\n'
    assert set_aliases(block, []) == "a: 1\naliases: []\n\nb: 2\n"
    commented = "a: 1\n# aliases:\n#   - 'x = ls'\n"
    assert set_aliases(commented, ["z = ls"]).startswith(commented.rstrip("\n")) and "\naliases:\n" in set_aliases(commented, ["z = ls"])
