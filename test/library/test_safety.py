"""What the review of the library file found: the backup before an upgrade, `@` folders, `check --fix` on a disk that is gone, previews."""

import os
import sqlite3

import pytest

from bdnex.library import AlbumInfo, Library
from bdnex.library.database import BackupError
from bdnex.library.schema import MIGRATIONS


def old_library(path, version=12):
    """A library file as an older bdnex left it: the tables of schema ``version`` only, and one album so that there is something to lose."""
    conn = sqlite3.connect(path)
    for statements in MIGRATIONS[:version]:
        conn.executescript(statements)
    conn.execute("INSERT INTO albums(kind, title, source, source_id) VALUES ('oneshot', 'Un', 'bedetheque', '1')")
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()
    return path


# ------------------------------------------------------------------------------------------------ the backup
def test_a_backup_that_fails_stops_the_upgrade_and_leaves_no_half_copy(tmp_path, monkeypatch):
    path = old_library(tmp_path / "lib.sqlite")
    before = path.read_bytes()
    real = sqlite3.connect

    def refusing(name, *args, **kwargs):  # the copy cannot be written (a full disk, a read-only folder)
        if str(name).endswith(".part"):
            raise sqlite3.OperationalError("database or disk is full")
        return real(name, *args, **kwargs)

    import bdnex.library.database as database

    monkeypatch.setattr(database.sqlite3, "connect", refusing)
    with pytest.raises(BackupError, match="nothing was changed"):
        Library(path)
    monkeypatch.undo()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lib.sqlite"]  # no `.bak-v12`, no `.part`: nothing a later start could trust
    assert path.read_bytes() == before  # and the library was not upgraded


def test_a_good_backup_is_kept_whole_and_the_library_is_upgraded(tmp_path):
    path = old_library(tmp_path / "lib.sqlite")
    with Library(path) as library:
        assert library.schema_version == len(MIGRATIONS)
    backup = tmp_path / "lib.sqlite.bak-v12"
    assert backup.exists() and not (tmp_path / "lib.sqlite.bak-v12.part").exists()
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 12 and old.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 1
    old.close()


def test_a_backup_an_earlier_version_cut_short_is_set_aside_and_made_again(tmp_path):
    path = old_library(tmp_path / "lib.sqlite")
    (tmp_path / "lib.sqlite.bak-v12").write_bytes(b"SQLite format 3\x00 cut short")  # what a full disk left, and was trusted for ever
    with Library(path):
        pass
    assert (tmp_path / "lib.sqlite.bak-v12.unusable").exists()
    good = sqlite3.connect(tmp_path / "lib.sqlite.bak-v12")
    assert good.execute("PRAGMA user_version").fetchone()[0] == 12 and good.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 1
    good.close()


def test_too_little_room_for_the_copy_is_said_before_anything_is_done(tmp_path, monkeypatch):
    import shutil

    path = old_library(tmp_path / "lib.sqlite")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(BackupError, match="free some room"):
        Library(path)
    assert not (tmp_path / "lib.sqlite.bak-v12").exists()


# ------------------------------------------------------------------------------------------------ paths
def test_a_collection_folder_named_with_an_at_sign_is_not_read_as_a_source(tmp_path):
    root = tmp_path / "comics"
    (root / "@Archive").mkdir(parents=True)
    path = root / "@Archive" / "x.cbz"
    path.write_bytes(b"x" * 10)
    db = tmp_path / "lib.sqlite"
    with Library(db) as library:  # a library from before `collection_path` was set: the path is stored absolute
        library.conn.execute("INSERT INTO files(path, md5, size, format) VALUES (?, 'm', 10, 'cbz')", (str(path),))
        library.conn.commit()
    with Library(db, root=root) as library:  # opened with the collection: the path is stored relative
        assert library.conn.execute("SELECT path FROM files").fetchone()[0] == "./@Archive/x.cbz"  # not `@Archive/x.cbz`, a source
        assert library.get_file(str(path))["path"] == str(path)  # and it resolves, so `ls` and `check` work
        assert [r["path"] for r in library.query([], files=True)] == [str(path)]


def test_a_library_path_with_a_question_mark_opens_read_only(tmp_path):
    path = tmp_path / "my?lib#1.sqlite"
    with Library(path):
        pass
    with Library(path, readonly=True) as library:
        assert library.schema_version == len(MIGRATIONS)


# ------------------------------------------------------------------------------------------------ check --fix
def _library_with_settings(tmp_path):
    from bdnex.config import build_settings

    settings = build_settings({"config_version": 2, "collection_path": str(tmp_path / "comics"), "library": str(tmp_path / "lib.sqlite"),
                               "cover": {"dir": str(tmp_path / "covers")}})[0]
    (tmp_path / "comics").mkdir()
    return Library(tmp_path / "lib.sqlite", root=tmp_path / "comics"), settings


def test_check_fix_does_not_forget_covers_and_trash_when_their_disk_is_not_there(tmp_path):
    from bdnex.library import health

    library, settings = _library_with_settings(tmp_path)
    for n in range(12):
        album = library.add_album(AlbumInfo("bedetheque", str(n), f"Album {n}", language="fr"))
        library.set_cover_file(album, f"bedetheque/{n}/cover.jpg")
        library.add_trash(f"/x/{n}.cbr", str(tmp_path / "trash" / f"{n}.cbr"), "converted")
    # the covers folder and the trash are on a disk that is not mounted: no file is found, none of 12 records is real news
    done = health.fix(library, settings, health.check(library, settings))
    assert any("kept the 12 cover record(s)" in line for line in done)
    assert any("kept the 12 trash record(s)" in line for line in done)
    assert library.conn.execute("SELECT COUNT(*) FROM albums WHERE cover_file IS NOT NULL").fetchone()[0] == 12
    assert len(library.trash_entries()) == 12  # `bdnex trash --restore` still has them
    library.close()


def test_check_fix_still_forgets_a_few_covers_that_are_really_gone(tmp_path):
    from bdnex.library import health

    library, settings = _library_with_settings(tmp_path)
    (settings.cover_path / "bedetheque" / "1").mkdir(parents=True)
    kept = library.add_album(AlbumInfo("bedetheque", "1", "Un", language="fr"))
    (settings.cover_path / "bedetheque" / "1" / "cover.jpg").write_bytes(b"x")
    library.set_cover_file(kept, "bedetheque/1/cover.jpg")
    lost = library.add_album(AlbumInfo("bedetheque", "2", "Deux", language="fr"))
    library.set_cover_file(lost, "bedetheque/2/cover.jpg")
    done = health.fix(library, settings, health.check(library, settings))
    assert any("forgot 1 cover file(s)" in line for line in done)
    assert library.get_album(lost)["cover_file"] is None and library.get_album(kept)["cover_file"]
    library.close()


def test_check_fix_leaves_a_part_file_of_yours_in_the_collection_alone(tmp_path):
    from bdnex.library import health

    library, settings = _library_with_settings(tmp_path)
    mine = tmp_path / "comics" / "Series" / "Tome 1.cbz.part"  # a download that is still going on
    mine.parent.mkdir()
    mine.write_bytes(b"half")
    ours = tmp_path / "comics" / "Series" / "Tome 2.cbz.bdnex-tmp"  # bdnex's own, left by an interrupted write
    ours.write_bytes(b"half")
    cover_part = settings.cover_path / "bedetheque" / "5" / "cover.jpg.part"
    cover_part.parent.mkdir(parents=True)
    cover_part.write_bytes(b"half")
    health.fix(library, settings, health.check(library, settings))
    assert mine.exists() and not ours.exists() and not cover_part.exists()
    library.close()


# ------------------------------------------------------------------------------------------------ relocate
def test_relocating_a_source_checks_that_the_new_folder_is_there_and_not_another_sources(tmp_path):
    library, settings = _library_with_settings(tmp_path)
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        library.add_root(name, tmp_path / name)
    (tmp_path / "new").mkdir()
    assert "not a folder" in library.relocate(str(tmp_path / "a"), str(tmp_path / "typo"))["error"]
    assert "another source" in library.relocate(str(tmp_path / "a"), str(tmp_path / "b"))["error"]
    assert library.roots["a"] == str(tmp_path / "a")  # nothing changed
    assert library.relocate(str(tmp_path / "a"), str(tmp_path / "new"))["source"] == "a" and library.roots["a"] == str(tmp_path / "new")
    library.close()


# ------------------------------------------------------------------------------------------------ previews
def test_reset_and_relocate_previews_do_not_open_the_library_for_writing(tmp_path, monkeypatch):
    import io

    from bdnex.ui import relocate_cmd, reset_cmd

    (tmp_path / "comics").mkdir()
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(f"config_version: 2\ncollection_path: {tmp_path / 'comics'}\nlibrary: {tmp_path / 'lib.sqlite'}\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    old_library(tmp_path / "lib.sqlite", version=12)  # older than this bdnex: opening it for writing would back it up and upgrade it
    before = (tmp_path / "lib.sqlite").read_bytes()
    for run, argv in ((reset_cmd.run, ["-n"]), (relocate_cmd.run, ["-n", "/old", "/new"])):
        try:
            run(argv, out=io.StringIO())
        except Exception:  # a read-only open of an older file says so: it does not upgrade it
            pass
    assert (tmp_path / "lib.sqlite").read_bytes() == before and not (tmp_path / "lib.sqlite.bak-v12").exists()


def test_relocating_a_folder_also_follows_the_trash_the_undo_list_and_the_staged_files(tmp_path):
    library, settings = _library_with_settings(tmp_path)
    old, new = tmp_path / "inbox", tmp_path / "moved"
    for folder in (old, new):
        folder.mkdir()
    path = old / "a.cbz"
    path.write_bytes(b"x" * 10)
    library.add_file(str(path))
    library.add_trash(str(old / "gone.cbr"), str(tmp_path / "trash" / "gone.cbr"), "converted")
    library.record_move("run1", str(old / "a.cbz"), str(old / "b.cbz"))
    library.conn.execute("INSERT INTO origins(file_id, origin) SELECT id, ? FROM files", (library._store(str(old / "src.cbz")),))
    library.stage_file(str(path), origin=str(old / "elsewhere.cbz"))
    shutil_path = str(new / "a.cbz")
    os.replace(path, shutil_path)
    result = library.relocate(str(old), str(new))
    assert result["files"] == 1 and result["other"] == 5  # trash.path, the two operations paths, the origin, the staged origin
    assert library.trash_entries()[0]["path"] == str(new / "gone.cbr")  # `trash --restore` puts it back in the folder that is there
    move = library.moves_of("run1")[0]
    assert (move["old_path"], move["new_path"]) == (str(new / "a.cbz"), str(new / "b.cbz"))  # and so does `undo`
    assert library.conn.execute("SELECT origin FROM staged_files").fetchone()[0] == str(new / "elsewhere.cbz")
    library.close()


# ------------------------------------------------------------------ two processes opening an old library at once
def test_two_processes_opening_an_old_library_at_once_both_end_up_with_the_current_schema(tmp_path):
    import multiprocessing

    from bdnex.library import Library
    from bdnex.library.database import SCHEMA_VERSION
    from bdnex.library.schema import MIGRATIONS

    path = tmp_path / "old.sqlite"
    import sqlite3

    conn = sqlite3.connect(path)  # a library as an older bdnex left it: schema v10
    for statement in MIGRATIONS[:10]:
        conn.executescript(statement)
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()

    def open_it(queue):
        try:
            with Library(path) as library:
                queue.put(("ok", library.schema_version))
        except Exception as err:
            queue.put(("error", repr(err)))

    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    workers = [context.Process(target=open_it, args=(queue,)) for _ in range(4)]
    for worker in workers:
        worker.start()
    results = [queue.get(timeout=60) for _ in workers]
    for worker in workers:
        worker.join(10)
    assert results == [("ok", SCHEMA_VERSION)] * 4, results  # the second one used to fail on a column that was already there


# ------------------------------------------------------------------ `::regex`
def test_a_regex_that_can_take_for_ever_is_refused_and_a_plain_one_is_not():
    import pytest

    from bdnex.library.query import QueryError, check_regex, regexp

    for bad in (r"(a+)+$", r"(.*)*x", r"(\w+\s?)+$", "x" * 301):
        with pytest.raises(QueryError):
            check_regex(bad)
    for good in (r"^(a|b)+$", r"t\d+", r"^nains?\b", r"(?i)redwin|forge", r"[a-z]+"):
        check_regex(good)
    long_value = "a" * 100000 + "!"
    import time

    started = time.monotonic()
    regexp(r"^(a|aa)+$", long_value)  # a value is cut at 2000 characters: it can not run for ever on a summary of pages
    assert time.monotonic() - started < 5
