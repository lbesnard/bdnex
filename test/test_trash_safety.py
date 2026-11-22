"""Findings of the review of the trash: a record is data (never a reason to delete or write elsewhere), a trash that is not mounted keeps its list."""

import os
import shutil

import pytest

from bdnex import trash

from test_versions_trash import lib_env  # noqa: F401  (the fixture: a library, a settings, one registered file)


def trashed(lib_env):
    settings, library, path, album, root = lib_env
    trash.move_to_trash(library, settings, path, "replaced")
    return settings, library, path, root


def test_a_record_that_points_outside_the_trash_never_deletes_what_it_names(lib_env, make_cbz):
    settings, library, path, album, root = lib_env
    precious = make_cbz("precious.cbz")
    library.add_trash(path, precious, "a record from another library", album_id=album)
    problems = []
    count, _ = trash.empty(library, library.trash_entries(), settings, problems)
    assert count == 0 and os.path.exists(precious)  # the comic it names is still there
    assert library.trash_entries() == [] and "not in the trash folder" in problems[0]  # the bad record is dropped
    entry = library.add_trash(path, precious, "again", album_id=album)
    with pytest.raises(trash.TrashError, match="not in the trash"):
        trash.restore(library, entry, settings)
    assert os.path.exists(precious) and library.get_trash(entry) is not None  # not moved, the record is kept


def test_an_unmounted_trash_keeps_its_records_instead_of_forgetting_the_files(lib_env):
    settings, library, path, root = trashed(lib_env)
    gone = settings.trash_path
    shutil.move(str(gone), str(gone) + ".away")  # the disk is not there
    problems = []
    assert trash.empty(library, library.trash_entries(), settings, problems) == (0, 0)
    assert len(library.trash_entries()) == 1 and "not there" in problems[0]  # it used to delete the record: the files could never come back
    (entry,) = library.trash_entries()
    with pytest.raises(trash.TrashError, match="not there"):
        trash.restore(library, entry["id"], settings)
    shutil.move(str(gone) + ".away", str(gone))  # mounted again
    assert trash.restore(library, entry["id"], settings) == path and os.path.exists(path)


def test_a_file_that_can_not_be_deleted_keeps_its_record_and_the_others_go_on(lib_env, make_cbz, monkeypatch):
    settings, library, path, album, root = lib_env
    other = root / "S" / "U.cbr"
    shutil.copy(make_cbz("u.cbz", pages=4), other)
    library.add_file(str(other), album)
    trash.move_to_trash(library, settings, path, "one")
    trash.move_to_trash(library, settings, str(other), "two")
    first, second = library.trash_entries()
    real = os.unlink
    from pathlib import Path

    original = Path.unlink

    def stuck(self, *args, **kwargs):
        if self.name == "T.cbr":
            raise PermissionError("read-only file system")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", stuck)
    problems = []
    count, _ = trash.empty(library, library.trash_entries(), settings, problems)
    assert count == 1 and len(library.trash_entries()) == 1 and "read-only file system" in problems[0]  # the stuck one stays listed


def test_a_file_is_not_restored_into_a_read_only_source(lib_env, make_cbz, tmp_path):
    from bdnex.config import build_settings

    settings, library, path, album, root = lib_env
    nas = tmp_path / "nas"
    nas.mkdir()
    copied = nas / "old.cbz"
    shutil.copy(make_cbz("z.cbz"), copied)
    settings = build_settings({"config_version": 2, "collection_path": str(root), "library": str(settings.library_path),
                               "import": {"sources": [{"path": str(nas), "mode": "copy"}]}})[0]
    library.add_file(str(copied), album)
    trash.move_to_trash(library, settings, str(copied), "replaced")  # (done by an older bdnex)
    (entry,) = library.trash_entries()
    with pytest.raises(trash.TrashError, match="read-only source"):
        trash.restore(library, entry["id"], settings)
    assert not copied.exists() and library.get_trash(entry["id"]) is not None


def test_a_failed_registration_puts_the_file_back_in_the_trash(lib_env, monkeypatch):
    settings, library, path, root = trashed(lib_env)
    (entry,) = library.trash_entries()
    monkeypatch.setattr(type(library), "add_file", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked")))
    with pytest.raises(RuntimeError):
        trash.restore(library, entry["id"], settings)
    assert os.path.exists(entry["trash_path"]) and not os.path.exists(path) and library.get_trash(entry["id"]) is not None


def test_the_move_is_recorded_first_and_a_failed_move_leaves_no_record(lib_env, monkeypatch):
    settings, library, path, album, root = lib_env
    monkeypatch.setattr(type(library), "add_trash", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked")))
    with pytest.raises(RuntimeError):
        trash.move_to_trash(library, settings, path, "x")
    assert os.path.exists(path) and library.get_file(path) is not None  # nothing was moved before it was written down
    monkeypatch.undo()
    monkeypatch.setattr(trash.shutil, "move", lambda *a, **k: (_ for _ in ()).throw(OSError("no space left")))
    with pytest.raises(OSError):
        trash.move_to_trash(library, settings, path, "x")
    assert library.trash_entries() == [] and os.path.exists(path)  # no record of a file that is not in the trash
