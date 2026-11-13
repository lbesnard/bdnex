"""Comparing versions of an album, and the trash."""

import io
import os
import shutil

import pytest
import yaml

from bdnex import trash
from bdnex.config import build_settings
from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.ui import trash_cmd
from bdnex.versions import (DIFFERENT, IDENTICAL, KEEP_EXISTING, KEEP_NEW, PROBABLY_SAME, SAME_BOOK, FileFacts,
                            compare, facts_of, relation, table)

PREFER = ["cbz", "cbr", "pdf"]


def facts(**kw):
    base = dict(path="/c/a.cbz", format="cbz", size=100 * 1048576, pages=20, corrupted=False, md5="aa", cover=0b1010)
    return FileFacts(**{**base, **kw})


# ----------------------------------------------------------------------- comparing
def test_identical_bytes_are_recognised_whatever_the_name():
    assert relation(facts(), facts(path="/d/b.cbz")) == IDENTICAL


def test_same_pages_and_same_cover_is_the_same_book_in_another_container():
    assert relation(facts(), facts(format="cbr", md5="bb", cover=0b1011)) == SAME_BOOK


def test_same_pages_without_a_cover_to_compare_is_only_probably_the_same():
    assert relation(facts(), facts(md5="bb", cover=None)) == PROBABLY_SAME


def test_another_cover_or_page_count_is_a_different_version():
    far = (1 << 40) - 1
    assert relation(facts(), facts(md5="bb", cover=far)) == DIFFERENT
    assert relation(facts(), facts(md5="bb", pages=21)) == DIFFERENT


@pytest.mark.parametrize("existing, new, expected, reason", [
    (facts(), facts(md5="b", path="/n.cbz"), KEEP_EXISTING, "nothing tells them apart"),            # a tie keeps what is there
    (facts(format="cbr"), facts(md5="b"), KEEP_NEW, "cbz is preferred to cbr"),
    (facts(), facts(format="cbr", md5="b"), KEEP_EXISTING, "cbz is preferred to cbr"),
    (facts(pages=20), facts(pages=24, md5="b"), KEEP_NEW, "24 pages against 20"),
    (facts(pages=24), facts(pages=20, md5="b"), KEEP_EXISTING, "24 pages against 20"),
    (facts(corrupted=True, pages=30), facts(pages=20, md5="b"), KEEP_NEW, "the existing file is damaged"),
    (facts(), facts(corrupted=True, pages=30, md5="b"), KEEP_EXISTING, "the new file is damaged"),
])
def test_the_suggestion(existing, new, expected, reason):
    result = compare([existing], new, PREFER)
    assert result.suggestion == expected and reason in result.reason


def test_with_several_existing_versions_the_best_one_is_the_reference():
    versions = [facts(format="pdf", path="/c/a.pdf"), facts(format="cbz", path="/c/b.cbz", pages=30)]
    assert compare(versions, facts(pages=25, md5="n"), PREFER).suggestion == KEEP_EXISTING


def test_the_table_lays_the_files_side_by_side():
    text = table(compare([facts(path="/lib/Series/a.cbz")], facts(path="/dl/a.cbr", format="cbr", md5="b"), PREFER), root="/lib")
    assert "existing  " in text and "Series/a.cbz" in text and "/lib/Series" not in text  # relative to the library
    assert "new       /dl/a.cbr" in text and "cbr · 20 pages · 100.0 MiB · ok" in text
    assert "same book" in text and "suggested: keep the existing file" in text


def test_the_table_stays_readable_with_long_paths_and_several_versions():
    long = "/very/long/" + "folder/" * 12 + "a.cbz"
    text = table(compare([facts(path="/lib/a.cbz"), facts(path="/lib/_versions/a.cbz", spare=True, pages=10)],
                         facts(path=long, md5="n"), PREFER), root="/lib")
    assert "existing 1" in text and "existing 2" in text and "spare version" in text and long in text
    assert max(len(line) for line in text.splitlines() if "·" in line) < 60  # details never get stretched by a path


def test_facts_of_a_registered_and_a_new_file(make_cbz):
    library = Library(":memory:")
    registered, fresh = make_cbz("a.cbz", pages=4), make_cbz("b.cbz", pages=6)
    library.add_file(registered)
    one, two = facts_of(library, registered), facts_of(library, fresh)
    assert (one.pages, one.format, two.pages) == (4, "cbz", 6) and one.md5 != two.md5 and library.get_file(fresh) is None


# --------------------------------------------------------------------------- trash
@pytest.fixture
def lib_env(tmp_path, make_cbz):
    root = tmp_path / "comics"
    root.mkdir()
    settings = build_settings({"config_version": 2, "collection_path": str(root), "library": str(tmp_path / "l.sqlite")})[0]
    library = Library.for_settings(settings)
    album = library.add_album(AlbumInfo("b", "1", "T", series=SeriesInfo("b", "s", "S"), volume=1))
    path = root / "S" / "T.cbr"
    path.parent.mkdir()
    shutil.copy(make_cbz("x.cbz"), path)
    library.add_file(str(path), album)
    return settings, library, str(path), album, root


def test_moving_to_the_trash_keeps_the_file_and_a_record(lib_env):
    settings, library, path, album, root = lib_env
    entry = trash.move_to_trash(library, settings, path, "replaced by x")
    assert not os.path.exists(path) and library.get_file(path) is None
    (record,) = library.trash_entries()
    assert record["id"] == entry and record["album_id"] == album and record["reason"] == "replaced by x"
    assert record["path"] == path and record["trash_path"].startswith(str(root / ".bdnex-trash")) and os.path.exists(record["trash_path"])
    assert record["format"] == "cbz" and record["size"] > 0 and record["md5"]  # detected from the content


def test_two_files_with_the_same_name_do_not_overwrite_each_other_in_the_trash(lib_env, make_cbz):
    settings, library, path, album, root = lib_env
    trash.move_to_trash(library, settings, path, "one")
    again = root / "S" / "T.cbr"
    shutil.copy(make_cbz("y.cbz", pages=5), again)
    library.add_file(str(again), album)
    trash.move_to_trash(library, settings, str(again), "two")
    paths = [e["trash_path"] for e in library.trash_entries()]
    assert len(set(paths)) == 2 and all(os.path.exists(p) for p in paths)


def test_a_file_the_library_does_not_know_can_be_trashed_too(lib_env, make_cbz):
    settings, library, _, _, root = lib_env
    stray = root / "stray.cbz"
    shutil.copy(make_cbz("s.cbz"), stray)
    trash.move_to_trash(library, settings, str(stray), "in the way")
    assert library.trash_entries()[0]["album_id"] is None and not stray.exists()


def test_restore_puts_it_back_and_registers_it(lib_env):
    settings, library, path, album, root = lib_env
    entry = trash.move_to_trash(library, settings, path, "r")
    assert trash.restore(library, entry) == path
    assert os.path.exists(path) and library.get_file(path)["album_id"] == album and library.trash_entries() == []


def test_restore_never_overwrites(lib_env, make_cbz):
    settings, library, path, album, root = lib_env
    entry = trash.move_to_trash(library, settings, path, "r")
    shutil.copy(make_cbz("n.cbz"), path)  # something else took the place
    restored = trash.restore(library, entry)
    assert restored != path and restored.endswith("T (2).cbr") and os.path.exists(path) and os.path.exists(restored)


def test_restoring_something_that_vanished_drops_its_record(lib_env):
    settings, library, path, _, _ = lib_env
    entry = trash.move_to_trash(library, settings, path, "r")
    os.remove(library.get_trash(entry)["trash_path"])
    assert trash.restore(library, entry) is None and library.trash_entries() == []
    with pytest.raises(KeyError):
        trash.restore(library, entry)


def test_emptying_deletes_files_records_and_the_dated_folders(lib_env):
    settings, library, path, _, root = lib_env
    trash.move_to_trash(library, settings, path, "r")
    count, freed = trash.empty(library, library.trash_entries())
    assert count == 1 and freed > 0 and library.trash_entries() == []
    assert list((root / ".bdnex-trash").iterdir()) == []


def test_old_entries_can_be_selected_by_age(lib_env):
    settings, library, path, _, _ = lib_env
    trash.move_to_trash(library, settings, path, "r")
    assert library.trash_entries(older_than_days=30) == []
    library.conn.execute("UPDATE trash SET trashed = datetime('now', '-40 days')")
    assert len(library.trash_entries(older_than_days=30)) == 1


def test_scans_do_not_see_the_trash(lib_env):
    settings, library, path, _, root = lib_env
    trash.move_to_trash(library, settings, path, "r")
    from bdnex.library.scan import iter_comic_files
    assert list(iter_comic_files(str(root))) == []


def test_without_a_library_folder_the_trash_sits_beside_the_database(tmp_path):
    settings = build_settings({"config_version": 2, "library": str(tmp_path / "db" / "l.sqlite")})[0]
    assert settings.trash_path == tmp_path / "db" / "trash"


# ------------------------------------------------------------------------- command
@pytest.fixture
def cmd(tmp_path, monkeypatch, make_cbz):
    root = tmp_path / "comics"
    root.mkdir()
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "collection_path": str(root), "library": str(tmp_path / "l.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    settings = build_settings(yaml.safe_load(conf.read_text()))[0]
    with Library.for_settings(settings) as library:
        path = root / "a.cbz"
        shutil.copy(make_cbz("a.cbz"), path)
        library.add_file(str(path))
        trash.move_to_trash(library, settings, str(path), "replaced by Downloads/a.cbr")
    return root, path


class Answer:
    def __init__(self, value):
        self.value = value

    def confirm(self, message):
        return self.value


def run(argv, answer=True):
    out = io.StringIO()
    return trash_cmd.run(argv, out=out, prompter=Answer(answer)), out.getvalue()


def test_listing_shows_the_reason_and_where_it_is(cmd):
    code, text = run([])
    assert code == 0 and "replaced by Downloads/a.cbr" in text and "1 file(s)" in text and ".bdnex-trash" in text
    assert "--restore" in text


def test_restore_command(cmd):
    root, path = cmd
    code, text = run(["--restore", "1", "-n"])
    assert "Would restore" in text and not path.exists()
    code, text = run(["--restore", "1"])
    assert code == 0 and path.exists() and "Restored" in text
    assert run(["--restore", "1"])[0] == 1


def test_empty_command_asks_and_deletes(cmd):
    root, path = cmd
    assert run(["--empty"], answer=False)[0] == 1 and len(list((root / ".bdnex-trash").rglob("*.cbz"))) == 1
    code, text = run(["--empty", "-y"])
    assert code == 0 and "Deleted 1 file(s)" in text and not list((root / ".bdnex-trash").rglob("*.cbz"))
    assert "empty" in run([])[1].lower()


def test_empty_older_than_keeps_recent_files(cmd):
    root, _ = cmd
    code, text = run(["--empty", "--older-than", "30", "-y"])
    assert "Nothing to empty" in text and len(list((root / ".bdnex-trash").rglob("*.cbz"))) == 1


def test_trash_place_and_reimport_need_restore():
    import pytest
    from bdnex.ui import trash_cmd
    for flag in ("--place", "--reimport"):
        with pytest.raises(SystemExit):
            trash_cmd.run([flag])
