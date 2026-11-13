import pytest
import yaml

from bdnex import ui


@pytest.fixture
def conf(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"config_version": 2, "collection_path": "~/c"}))
    monkeypatch.setenv("BDNEX_CONFIG", str(path))


def run_main(*argv):
    with pytest.raises(SystemExit) as exit_:
        ui.main(list(argv))
    return exit_.value.code


def test_no_arguments_shows_usage(conf, capsys):
    assert run_main() == 0
    out = capsys.readouterr().out
    for command in ("import", "ls", "write", "move", "config"):
        assert command in out


def test_help_flag(conf, capsys):
    assert run_main("--help") == 0 and "usage: bdnex" in capsys.readouterr().out


def test_legacy_file_and_dir_flags_run_import_with_write(conf, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr("bdnex.ui.import_cmd.run", lambda argv: seen.append(argv) or 0)
    assert run_main("-f", "album.cbz") == 0
    assert run_main("-d", "/comics") == 0
    assert seen == [["-W", "album.cbz"], ["-W", "/comics"]]
    assert "now run `bdnex import -W`" in capsys.readouterr().err


def test_legacy_init_flag_explains(conf, capsys):
    assert run_main("-i") == 0
    assert "no longer needed" in capsys.readouterr().err


def test_invalid_config_blocks_every_command(tmp_path, monkeypatch, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("config_version: 2\noops: 1\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(path))
    assert run_main("ls") == 1 and "oops" in capsys.readouterr().err
    assert run_main("config", "--path") == 0  # but `config` itself always works
