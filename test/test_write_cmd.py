import io
import os
import zipfile

import pytest
import yaml

from bdnex.importer.bedetheque import Bedetheque
from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.ui import import_cmd, write_cmd
from helpers import ALBUM_URL, FakeWeb


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db), "directory": str(tmp_path / "comics"),
                                    "bedetheque": {"delay": [0, 0]}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(db) as lib:
        a = lib.add_album(AlbumInfo("bedetheque", "1", "Redwin de la Forge",
                                    series=SeriesInfo("bedetheque", "47467", "Nains"), volume=1, language="fr",
                                    credits={"Writer": ["Jarry, Nicolas"]}))
        b = lib.add_album(AlbumInfo("bedetheque", "2", "Bran de Pierre",
                                    series=SeriesInfo("bedetheque", "47467", "Nains"), volume=2, language="fr",
                                    credits={"Writer": ["Jarry, Nicolas"]}))
        files = {"a": make_cbz("one.cbz", pages=3), "b": make_cbz("two.cbz", pages=4)}
        lib.add_file(files["a"], a)
        lib.add_file(files["b"], b)
        lib.add_file(make_cbz("loose.cbz", pages=5))
    return tmp_path, files


def run(command, *argv):
    out = io.StringIO()
    code = write_cmd.run(command, list(argv), out=out)
    return code, out.getvalue()


def has_comicinfo(path):
    with zipfile.ZipFile(path) as zf:
        return "ComicInfo.xml" in zf.namelist()


def test_write_all_tagged_files(env):
    tmp, files = env
    code, text = run("write", "--force")
    assert code == 0 and "2 written" in text
    assert has_comicinfo(files["a"]) and has_comicinfo(files["b"])
    assert not has_comicinfo(str(tmp / "loose.cbz"))  # never matched: left alone


def test_write_with_query(env):
    tmp, files = env
    code, text = run("write", "--force", "volume:2")
    assert "1 written" in text and has_comicinfo(files["b"]) and not has_comicinfo(files["a"])


def test_write_pretend(env):
    tmp, files = env
    code, text = run("write", "-n", "--force")
    assert code == 0 and "2 pretend" in text and not has_comicinfo(files["a"])


def test_move_into_library_tree_and_copy(env):
    tmp, files = env
    code, text = run("move", "--force", "volume:1")
    dest = tmp / "comics" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz"
    assert code == 0 and dest.exists() and not os.path.exists(files["a"])
    code, text = run("move", "--force", "--copy", "volume:2")
    assert "1 copied" in text and os.path.exists(files["b"])


def test_move_requires_a_directory(env, monkeypatch, tmp_path):
    conf = tmp_path / "nodir.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert run("move")[0] == 1


def test_errors(env, monkeypatch, tmp_path, capsys):
    assert run("write", "year:abc")[0] == 2 and "bad query" in capsys.readouterr().err
    conf = tmp_path / "none.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "missing.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert run("write")[0] == 1 and "bdnex import" in capsys.readouterr().err


def test_main_dispatches_write_and_move(monkeypatch, tmp_path):
    from bdnex import ui
    conf = tmp_path / "c.yaml"
    conf.write_text("config_version: 2\ndirectory: ~/c\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    seen = []
    monkeypatch.setattr("bdnex.ui.write_cmd.run", lambda cmd, argv: seen.append((cmd, argv)) or 0)
    for cmd in ("write", "move"):
        with pytest.raises(SystemExit):
            ui.main([cmd, "series:nains"])
    assert seen == [("write", ["series:nains"]), ("move", ["series:nains"])]


# ----------------------------------------------------------- import -W/--move/--copy
@pytest.fixture
def import_env(tmp_path, monkeypatch, make_cbz):
    def configure(extra=None):
        conf = tmp_path / "bdnex.yaml"
        conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"),
                                        "directory": str(tmp_path / "comics"),
                                        "bedetheque": {"delay": [0, 0]}, **(extra or {})}))
        monkeypatch.setenv("BDNEX_CONFIG", str(conf))

    configure()
    web = FakeWeb()
    monkeypatch.setattr(import_cmd, "http_fetch", web)
    monkeypatch.setattr(import_cmd, "build_fetcher", lambda settings: web)
    original = Bedetheque.album_info
    monkeypatch.setattr(Bedetheque, "album_info", lambda self, url, recache=False: original(self, ALBUM_URL, recache))
    monkeypatch.setattr("bdnex.importer.bedetheque.bedetheque.uniform", lambda a, b: 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    path = make_cbz("Nains - T01 - Redwin de la forge.cbz")
    return path, tmp_path, configure


def do_import(*argv):
    out = io.StringIO()
    code = import_cmd.run(list(argv), out=out)
    return code, out.getvalue()


def test_plain_import_does_not_touch_the_file(import_env):
    path, tmp, _ = import_env
    code, text = do_import(path)
    assert code == 0 and not has_comicinfo(path) and "Files:" not in text


def test_import_with_write_flag(import_env):
    path, tmp, _ = import_env
    code, text = do_import("-W", "--force", path)
    assert code == 0 and has_comicinfo(path) and "1 written" in text


def test_import_with_move_flag(import_env):
    path, tmp, _ = import_env
    code, text = do_import("--move", "--force", path)
    dest = tmp / "comics" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz"
    assert code == 0 and dest.exists() and not os.path.exists(path)
    with Library(tmp / "lib.sqlite") as lib:
        assert lib.get_file(str(dest)) is not None  # the library tracks the new location


def test_import_mode_copy_from_config(import_env):
    path, tmp, configure = import_env
    configure({"import": {"mode": "copy"}})
    code, text = do_import("--force", path)
    assert code == 0 and os.path.exists(path) and "1 copied" in text


def test_import_pretend_does_no_file_operations(import_env):
    path, tmp, _ = import_env
    code, text = do_import("-n", "--move", path)
    assert code == 0 and os.path.exists(path) and not (tmp / "comics").exists()


def test_import_move_without_directory_explains_and_keeps_the_import(import_env, capsys):
    path, tmp, configure = import_env
    conf = tmp / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp / "lib.sqlite"),
                                    "bedetheque": {"delay": [0, 0]}}))
    code, text = do_import("--move", path)
    assert code == 1 and os.path.exists(path)
    assert "directory" in capsys.readouterr().err
    with Library(tmp / "lib.sqlite") as lib:
        assert lib.stats()["albums"] == 1  # the match itself was recorded
