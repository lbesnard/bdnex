import io
import os

import pytest
import yaml

from bdnex.importer.bedetheque import Bedetheque
from bdnex.ui import import_cmd
from helpers import ALBUM_URL, FakeWeb


@pytest.fixture
def setup(tmp_path, monkeypatch, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"),
                                    "bedetheque": {"delay": [0, 0]}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    web = FakeWeb()
    import bdnex.importer.bedetheque as bdt_plugin
    monkeypatch.setattr(bdt_plugin, "http_fetch", web)
    monkeypatch.setattr(bdt_plugin, "build_fetcher", lambda settings: web)
    original = Bedetheque.album_info
    monkeypatch.setattr(Bedetheque, "album_info",
                        lambda self, url, recache=False: original(self, ALBUM_URL, recache))
    monkeypatch.setattr("bdnex.importer.bedetheque.bedetheque.uniform", lambda a, b: 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return make_cbz("Nains - T01 - Redwin de la forge.cbz"), tmp_path


def test_import_command_end_to_end(setup):
    path, tmp_path = setup
    out = io.StringIO()
    assert import_cmd.run([str(os.path.dirname(path))], out=out) == 0
    text = out.getvalue()
    assert "tagged" in text and "1 file(s): 1 tagged" in text

    from bdnex.library import Library
    with Library(tmp_path / "lib.sqlite") as lib:
        assert lib.stats()["albums"] == 1 and lib.get_file(path)["album_id"] is not None


def test_import_command_pretend_leaves_library_empty(setup):
    path, tmp_path = setup
    out = io.StringIO()
    assert import_cmd.run(["--pretend", path], out=out) == 0
    assert "pretend" in out.getvalue()
    from bdnex.library import Library
    with Library(tmp_path / "lib.sqlite") as lib:
        assert lib.stats()["files"] == 0 and lib.stats()["albums"] == 0


def test_import_command_rejects_invalid_config(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "bad.yaml"
    conf.write_text("config_version: 2\noops: 1\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert import_cmd.run(["x.cbz"]) == 1
    assert "oops" in capsys.readouterr().err


def test_main_dispatches_import(monkeypatch, tmp_path):
    from bdnex import ui
    conf = tmp_path / "c.yaml"
    conf.write_text("config_version: 2\ndirectory: ~/c\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    seen = {}
    monkeypatch.setattr("bdnex.ui.import_cmd.run", lambda argv: seen.setdefault("argv", argv) and 0)
    with pytest.raises(SystemExit) as exit_:
        ui.main(["import", "-n", "foo.cbz"])
    assert seen["argv"] == ["-n", "foo.cbz"] and exit_.value.code == 0


def test_import_command_shows_progress_on_stderr(setup, capsys):
    path, tmp_path = setup
    import_cmd.run([path], out=io.StringIO())
    err = capsys.readouterr().err
    assert "Preparing the bedetheque source" in err and "[1/1] Nains - T01 - Redwin de la forge.cbz" in err


def test_verbosity_levels():
    import logging
    from bdnex.ui.output import setup_logging
    for flags, level in ((0, logging.WARNING), (1, logging.INFO), (2, logging.DEBUG)):
        setup_logging(flags)
        assert logging.getLogger().level == level
    setup_logging(0)


def test_import_accepts_verbose_flag(setup):
    path, tmp_path = setup
    assert import_cmd.run(["-vv", "-n", path], out=io.StringIO()) == 0


def test_import_command_with_a_configured_foreign_source(tmp_path, monkeypatch, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"),
                                    "source": "helpers:create_fake_source"}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    out = io.StringIO()
    assert import_cmd.run(["--force", make_cbz("Maus.cbz")], out=out) == 0
    assert "1 file(s): 1 tagged" in out.getvalue()
    from bdnex.library import Library
    with Library(tmp_path / "lib.sqlite") as lib:
        assert lib.get_album(lib.find_album("fake", "200"))["title"] == "Maus"


def test_import_command_with_an_unknown_source(tmp_path, monkeypatch, capsys, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"), "source": "nope"}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert import_cmd.run([make_cbz("a.cbz")], out=io.StringIO()) == 1
    err = capsys.readouterr().err
    assert "unknown source 'nope'" in err and "bedetheque" in err and "config --edit" in err


def test_import_with_a_mistyped_fetcher_prints_a_message_not_a_traceback(tmp_path, monkeypatch, capsys, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"), "bedetheque": {
        "fetcher": "bdnex.importer.bedetheque.flaresolverr_fetch:flaresolverr_fetch"}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert import_cmd.run([make_cbz("a.cbz")], out=io.StringIO()) == 1
    err = capsys.readouterr().err
    assert "Did you mean 'bdnex.importer.bedetheque:flaresolverr_fetch'?" in err
    assert "bedetheque.fetcher" in err and "Traceback" not in err
