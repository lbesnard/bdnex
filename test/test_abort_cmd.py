"""``bdnex import`` aborted from the match question."""

import json
import os
import shutil

import pytest

from bdnex.importer.session import ImportAborted
from bdnex.ui.prompts import InquirerPrompter
from test_outside import NAME, do, library, world  # noqa: F401  (the fixture builds the library and a download folder)

PARTIAL_NAMES = ["Nains Redwin.cbz", "Nains Redwin (2).cbz", "Nains Redwin (3).cbz"]  # medium matches: they are asked


@pytest.fixture
def downloads(world, make_cbz):
    root, folder, comic, configure = world
    os.remove(comic)
    paths = []
    for number, name in enumerate(PARTIAL_NAMES):
        path = folder / name
        shutil.copy(make_cbz(f"src{number}.cbz", pages=3 + number), path)
        paths.append(str(path))
    paths.sort()  # the order the files are looked at
    configure(**{"import": {"outside": "move", "auto": False, "series": "never"}, "duplicates": {"action": "keep_both"}})
    return root, folder, paths


def answer(monkeypatch, abort_at):
    asked = []

    def choose(self, path, matches):
        asked.append(path)
        if len(asked) == abort_at:
            raise ImportAborted(path)
        return matches[0].candidate

    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr(InquirerPrompter, "choose_match", choose)
    return asked


def test_abort_says_where_it_stopped_and_how_to_continue(downloads, monkeypatch, tmp_path):
    root, folder, paths = downloads
    asked = answer(monkeypatch, abort_at=2)
    code, text = do(str(folder))
    assert code == 1 and asked == paths[:2]
    assert f"Aborted at {os.path.basename(paths[1])} (1 of 3 files looked at)" in text
    assert "no album page was fetched" in text and "run the same command again" in text
    with library(tmp_path) as lib:
        assert lib.get_file(paths[0])["album_id"] is not None and lib.get_file(paths[1]) is None
        assert json.loads(lib.get_meta("import_aborted"))["file"] == paths[1]


def test_nothing_is_moved_after_an_abort_even_with_outside_set_to_move(downloads, monkeypatch, tmp_path):
    root, folder, paths = downloads
    answer(monkeypatch, abort_at=2)
    code, text = do(str(folder))
    assert all(os.path.exists(p) for p in paths) and not list(root.rglob("*.cbz"))
    assert "left where they are" in text and "bdnex move staged:true" in text
    with library(tmp_path) as lib:
        assert [f["path"] for f in lib.staged_files()] == [paths[0]]  # the one decided before the abort can still be placed


def test_running_it_again_continues_and_places_the_rest(downloads, monkeypatch, tmp_path, capsys):
    root, folder, paths = downloads
    answer(monkeypatch, abort_at=2)
    do(str(folder))
    capsys.readouterr()
    asked = answer(monkeypatch, abort_at=99)
    code, text = do(str(folder))
    assert code == 0 and asked == paths[1:]  # the first file is not asked about again
    assert f"Resuming: the last import was aborted at {os.path.basename(paths[1])}" in capsys.readouterr().err
    with library(tmp_path) as lib:
        assert lib.get_meta("import_aborted") is None
        assert all(lib.get_file(p) is None or lib.get_file(p)["album_id"] is not None for p in paths)
