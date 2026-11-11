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
    conf.write_text(yaml.safe_dump({"config_version": 1, "library": str(tmp_path / "lib.sqlite"),
                                    "bedetheque": {"delay": [0, 0]}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    web = FakeWeb()
    monkeypatch.setattr(import_cmd, "http_fetch", web)
    monkeypatch.setattr(import_cmd, "build_fetcher", lambda settings: web)
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
    conf.write_text("config_version: 1\noops: 1\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert import_cmd.run(["x.cbz"]) == 1
    assert "oops" in capsys.readouterr().err


def test_main_dispatches_import(monkeypatch, tmp_path):
    from bdnex import ui
    conf = tmp_path / "c.yaml"
    conf.write_text("config_version: 1\ndirectory: ~/c\n")
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
    assert "Downloading the Bédéthèque sitemaps" in err and "[1/1] Nains - T01 - Redwin de la forge.cbz" in err


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
