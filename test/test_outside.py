"""Files imported from outside the library folder: ask once, move, copy or leave (and place them later)."""

import io
import os
import shutil

import pytest
import yaml

from bdnex.importer.bedetheque import Bedetheque
from bdnex.library import Library
from bdnex.ui import import_cmd, init_cmd, ls_cmd, write_cmd
from helpers import ALBUM_URL, FakeWeb

NAME = "Nains - T01 - Redwin de la forge.cbz"
IN_TREE = os.path.join("Series", "Nains", "Nains - T01 - Redwin de la Forge.cbz")


@pytest.fixture
def world(tmp_path, monkeypatch, make_cbz):
    """A library folder, a download folder with one comic, and a config pointing at the first."""
    root, downloads = tmp_path / "comics", tmp_path / "downloads"
    root.mkdir()
    downloads.mkdir()

    def configure(**overrides):
        settings = {"config_version": 2, "collection_path": str(root), "library": str(tmp_path / "lib.sqlite"),
                    "import": {"auto": True}, "bedetheque": {"delay": [0, 0]}}
        for key, value in overrides.items():
            settings[key] = {**settings.get(key, {}), **value} if isinstance(value, dict) else value
        (tmp_path / "bdnex.yaml").write_text(yaml.safe_dump(settings))
        monkeypatch.setenv("BDNEX_CONFIG", str(tmp_path / "bdnex.yaml"))

    configure()
    web = FakeWeb()
    import bdnex.importer.bedetheque as plugin
    monkeypatch.setattr(plugin, "http_fetch", web)
    monkeypatch.setattr(plugin, "build_fetcher", lambda settings: web)
    original = Bedetheque.album_info
    monkeypatch.setattr(Bedetheque, "album_info", lambda self, url, recache=False: original(self, ALBUM_URL, recache))
    monkeypatch.setattr("bdnex.importer.bedetheque.bedetheque.uniform", lambda a, b: 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert init_cmd.run([], out=io.StringIO(), bar_stream=io.StringIO()) == 0
    comic = downloads / NAME
    shutil.copy(make_cbz("src.cbz"), comic)
    return root, downloads, str(comic), configure


def do(*argv):
    out = io.StringIO()
    return import_cmd.run(list(argv), out=out), out.getvalue()


def library(tmp_path):
    return Library(tmp_path / "lib.sqlite", root=tmp_path / "comics")


def test_without_a_terminal_files_from_elsewhere_are_left_and_remembered(world, tmp_path):
    root, downloads, comic, _ = world
    code, text = do(str(downloads))
    assert code == 0 and os.path.exists(comic) and not list(root.rglob("*.cbz"))
    assert "No terminal to ask on" in text and "bdnex move staged:true" in text
    with library(tmp_path) as lib:
        (staged,) = lib.staged_files()
        assert staged["path"] == comic and staged["origin"] == comic and staged["album_id"] is not None


def test_staged_files_can_be_listed_and_placed_later(world, tmp_path):
    root, downloads, comic, _ = world
    do(str(downloads))
    out = io.StringIO()
    assert ls_cmd.run(["--files", "staged:true"], out=out) == 0 and comic in out.getvalue()
    out = io.StringIO()
    assert write_cmd.run("move", ["staged:true", "--force"], out=out) == 0
    assert (root / IN_TREE).exists() and not os.path.exists(comic)
    with library(tmp_path) as lib:
        assert lib.staged_files() == [] and lib.get_file(str(root / IN_TREE)) is not None
    out = io.StringIO()
    ls_cmd.run(["--files", "staged:true"], out=out)
    assert out.getvalue() == ""


@pytest.mark.parametrize("setting, moved, original_stays", [("move", True, False), ("copy", True, True), ("leave", False, True)])
def test_the_outside_setting_decides_without_asking(world, tmp_path, setting, moved, original_stays):
    root, downloads, comic, configure = world
    configure(**{"import": {"outside": setting}})
    code, text = do(str(downloads))
    assert code == 0 and (root / IN_TREE).exists() == moved and os.path.exists(comic) == original_stays
    with library(tmp_path) as lib:
        assert bool(lib.staged_files()) == (setting == "leave")
        assert lib.get_file(str(root / IN_TREE) if moved else comic)["album_id"] is not None


def test_no_move_overrides_the_setting(world, tmp_path):
    root, downloads, comic, configure = world
    configure(**{"import": {"outside": "move"}})
    code, text = do("--no-move", str(downloads))
    assert code == 0 and os.path.exists(comic) and not list(root.rglob("*.cbz"))
    with library(tmp_path) as lib:
        assert len(lib.staged_files()) == 1


def test_move_and_copy_flags_override_the_setting(world, tmp_path):
    root, downloads, comic, configure = world
    configure(**{"import": {"outside": "leave"}})
    assert do("--move", str(downloads))[0] == 0 and (root / IN_TREE).exists() and not os.path.exists(comic)


def test_the_question_is_asked_once_for_all_the_files_and_the_answer_is_used(world, tmp_path, monkeypatch, make_cbz):
    root, downloads, comic, _ = world
    shutil.copy(make_cbz("other.cbz"), downloads / "Nains - T01 - Redwin de la forge (2).cbz")
    asked = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("bdnex.ui.prompts.InquirerPrompter.choose_placement",
                        lambda self, count, directory: asked.append((count, directory)) or "leave")
    monkeypatch.setattr("bdnex.ui.prompts.InquirerPrompter.resolve_duplicate", lambda self, c, w, r=None: "skip")
    code, text = do(str(downloads))
    assert len(asked) == 1 and asked[0][1] == str(root) and asked[0][0] >= 1 and os.path.exists(comic)


def test_the_answer_move_moves_them(world, tmp_path, monkeypatch):
    root, downloads, comic, _ = world
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("bdnex.ui.prompts.InquirerPrompter.choose_placement", lambda self, count, directory: "move")
    code, text = do(str(downloads))
    assert code == 0 and (root / IN_TREE).exists() and not os.path.exists(comic)


def test_files_already_in_the_library_folder_are_never_asked_about(world, tmp_path, monkeypatch):
    root, downloads, comic, _ = world
    inside = root / "mine" / NAME
    inside.parent.mkdir()
    shutil.move(comic, inside)
    asked = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("bdnex.ui.prompts.InquirerPrompter.choose_placement", lambda self, c, d: asked.append(1) or "move")
    code, text = do(str(root))
    assert code == 0 and asked == [] and inside.exists()
    with library(tmp_path) as lib:
        assert lib.staged_files() == [] and lib.get_file(str(inside)) is not None


def test_import_mode_from_the_configuration_applies_to_everything_and_asks_nothing(world, tmp_path):
    root, downloads, comic, configure = world
    configure(**{"import": {"mode": "move"}})
    code, text = do(str(downloads))
    assert code == 0 and (root / IN_TREE).exists() and "No terminal" not in text


def test_a_preview_moves_and_stages_nothing(world, tmp_path):
    root, downloads, comic, configure = world
    configure(**{"import": {"outside": "move"}})
    assert do("-n", str(downloads))[0] == 0 and os.path.exists(comic)
    assert not (tmp_path / "lib.sqlite").exists() or not library(tmp_path).staged_files()


def test_without_a_library_folder_nothing_is_outside(world, tmp_path):
    root, downloads, comic, configure = world
    (tmp_path / "bdnex.yaml").write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"),
                                                         "import": {"auto": True}, "bedetheque": {"delay": [0, 0]}}))
    code, text = do(str(downloads))
    assert code == 0 and os.path.exists(comic) and "No terminal" not in text


def test_no_move_cannot_be_combined_with_move(world, capsys):
    root, downloads, comic, _ = world
    with pytest.raises(SystemExit):
        import_cmd.build_parser().parse_args(["--move", "--no-move", comic])


def test_without_a_path_the_library_folder_of_the_configuration_is_imported(world, tmp_path, capsys):
    root, downloads, comic, _ = world
    inside = root / "mine" / NAME
    inside.parent.mkdir()
    shutil.move(comic, inside)
    code, text = do()
    assert code == 0 and f"No folder given: importing your library folder, {root}" in capsys.readouterr().err
    assert "1 complete" in text
    with library(tmp_path) as lib:
        assert lib.get_file(str(inside))["album_id"] is not None


def test_a_download_folder_is_never_looked_at_unless_it_is_named(world, tmp_path):
    root, downloads, comic, _ = world  # the comic sits in the download folder, not in the library folder
    code, text = do()
    assert code == 0 and "0 file(s): nothing to do" in text
    with library(tmp_path) as lib:
        assert lib.get_file(comic) is None and os.path.exists(comic)


def test_fetch_alone_without_a_path_still_means_the_whole_library(world, tmp_path, capsys):
    root, downloads, comic, _ = world
    do("--match", "--auto=all", str(downloads))
    capsys.readouterr()
    code, text = do("--fetch")
    assert code == 0 and "No folder given" not in capsys.readouterr().err and "1 complete" in text


def _snapshot(*folders):
    """Every file under the folders: its bytes and its modification time, so any change shows."""
    import hashlib
    found = {}
    for folder in folders:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                found[str(path)] = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    return found


def test_import_database_only_never_changes_a_comic_even_with_the_worst_settings(world, tmp_path):
    """`import --database-only` fills the library and touches no file: not even with `import.mode: move`,
    a copy source and `duplicates.action: replace` in the configuration, and an identical file twice."""
    root, downloads, comic, configure = world
    inside = root / "mine" / NAME
    inside.parent.mkdir()
    shutil.copy(comic, inside)  # the same bytes twice: a duplicate
    configure(**{"import": {"mode": "move", "outside": "move", "sources": [{"path": str(downloads), "mode": "move"}]},
                 "duplicates": {"action": "replace"}})
    before = _snapshot(root, downloads)
    code, _ = do("--database-only", "--auto=all", str(root), str(downloads))
    assert code == 0
    assert _snapshot(root, downloads) == before  # same files, same bytes, same times: nothing moved, written or trashed
    with library(tmp_path) as lib:
        assert lib.get_file(str(inside)) is not None or lib.get_file(comic) is not None  # the library learned of one; the twin is a skipped duplicate


def test_database_only_refuses_the_options_that_change_files(world, capsys):
    root, downloads, comic, _ = world
    for flag in ("--write", "--move", "--copy"):
        with pytest.raises(SystemExit):
            import_cmd.run(["--database-only", flag, comic], out=io.StringIO())
