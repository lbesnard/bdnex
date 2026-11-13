"""bdnex check and bdnex duplicates."""

import io
import os

import pytest
import yaml

from bdnex.library import AlbumInfo, Library
from bdnex.library.health import check, duplicates, fix
from bdnex.ui import check_cmd, duplicates_cmd
from bdnex.config import build_settings


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db), "cover": {"dir": str(tmp_path / "covers")}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    settings = build_settings(yaml.safe_load(conf.read_text()))[0]
    return db, settings, make_cbz


def keys(findings):
    return {f.key for f in findings}


def test_a_healthy_library_has_nothing_to_report(env):
    db, settings, make_cbz = env
    with Library(db) as lib:
        lib.add_file(make_cbz("a.cbz"), lib.add_album(AlbumInfo("bedetheque", "1", "Un", language="fr")))
        assert check(lib, settings) == []
    out = io.StringIO()
    assert check_cmd.run([], out=out) == 0 and "consistent" in out.getvalue()


def test_check_finds_the_usual_problems(env, tmp_path):
    db, settings, make_cbz = env
    with Library(db) as lib:
        gone, changed, loose = make_cbz("gone.cbz"), make_cbz("changed.cbz"), make_cbz("loose.cbz")
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Un", language="fr"))
        lib.add_album(AlbumInfo("bedetheque", "2", "Sans fichier", language="fr"))
        for path in (gone, changed):
            lib.add_file(path, album)
        lib.add_file(loose)
        lib.flag_review(changed, "the cover differs")
        os.remove(gone)
        with open(changed, "ab") as fh:
            fh.write(b"more")
        covers = settings.cover_path
        (covers / "bedetheque").mkdir(parents=True)
        (covers / "bedetheque" / "orphan.jpg").write_bytes(b"x")
        (covers / "bedetheque" / "7.jpg.part").write_bytes(b"x")
        lib.set_cover_file(album, "bedetheque/lost.jpg")
        lib.add_trash("/x/a.cbr", str(tmp_path / "trash" / "a.cbr"), "converted")
        found = check(lib, settings)
    assert keys(found) >= {"missing", "changed", "unmatched", "review", "albums-without-file", "cover-missing",
                           "cover-orphans", "trash-dead", "leftovers", "cover-layout"}  # orphan.jpg: the old flat layout
    assert [f for f in found if f.key == "missing"][0].items == [gone]


def test_fix_only_settles_the_safe_things(env, tmp_path):
    db, settings, make_cbz = env
    with Library(db) as lib:
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Un", language="fr"))
        lib.set_cover_file(album, "bedetheque/lost.jpg")
        lib.add_trash("/x/a.cbr", str(tmp_path / "trash" / "a.cbr"), "converted")
        settings.cover_path.mkdir(parents=True)
        (settings.cover_path / "7.jpg.part").write_bytes(b"x")
        before = check(lib, settings)
        done = fix(lib, settings, before)
        assert len(done) >= 3 and keys(check(lib, settings)) == {"albums-without-file"}
        assert lib.get_album(album)["cover_file"] is None and lib.trash_entries() == []
        assert not (settings.cover_path / "7.jpg.part").exists()


def test_check_is_read_only_without_fix(env, tmp_path):
    db, settings, make_cbz = env
    with Library(db) as lib:
        lib.add_trash("/x/a.cbr", str(tmp_path / "trash" / "a.cbr"), "converted")
    before = db.read_bytes()
    out = io.StringIO()
    assert check_cmd.run([], out=out) == 1 and "[fixable]" in out.getvalue()
    assert db.read_bytes() == before


def test_duplicates_groups_identical_files_and_one_album_with_several_files(env, tmp_path):
    db, settings, make_cbz = env
    import shutil
    with Library(db) as lib:
        first = make_cbz("a.cbz", pages=3)
        copy = str(tmp_path / "copy.cbz")
        shutil.copy(first, copy)
        other = make_cbz("b.cbz", pages=5)
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Un", language="fr"))
        album2 = lib.add_album(AlbumInfo("bedetheque", "2", "Deux", language="fr"))
        lib.add_file(first, album)
        lib.add_file(copy, album2)
        lib.add_file(other, album2)
        groups = duplicates(lib)
    why = {g["why"]: [f["path"] for f in g["files"]] for g in groups}
    assert sorted(why["identical content"]) == sorted([first, copy])
    assert sorted(why["same album"]) == sorted([copy, other])
    out = io.StringIO()
    assert duplicates_cmd.run([], out=out) == 0 and "2 group(s)" in out.getvalue()


def test_spare_versions_are_not_duplicates_of_their_album(env):
    db, settings, make_cbz = env
    with Library(db) as lib:
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Un", language="fr"))
        a, b = make_cbz("a.cbz", pages=3), make_cbz("b.cbz", pages=4)
        lib.add_file(a, album)
        lib.add_file(b, album)
        lib.set_file_fields(b, spare=1)
        assert [g for g in duplicates(lib) if g["why"] == "same album"] == []


def test_cover_hashes_are_worked_out_in_several_processes_and_stored(env, make_image_pdf):
    db, settings, make_cbz = env
    with Library(db) as lib:
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Un", language="fr"))
        paths = [make_image_pdf(f"p{n}.pdf", pages=1)[0] for n in range(5)]
        for path in paths:
            lib.add_file(path, album)
        lib.set_file_fields(paths[0], spare=0)
        serial = duplicates(lib, compute_covers=True, workers=1)
        hashes = {path: lib.get_file(path)["cover_hash"] for path in paths}
        assert all(hashes.values()), "every file got the hash of its first page, stored in the library"
        lib.conn.execute("UPDATE files SET cover_hash = NULL")
        lib.conn.commit()
        parallel = duplicates(lib, compute_covers=True, workers=3)
        assert {path: lib.get_file(path)["cover_hash"] for path in paths} == hashes  # the same hashes
        assert [g["why"] for g in parallel] == [g["why"] for g in serial]
