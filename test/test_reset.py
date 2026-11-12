"""``bdnex reset`` keeps what was downloaded and removes everything about the collection."""

import glob
import io
import sqlite3

import pytest
import yaml

from bdnex.library import SCHEMA_VERSION, AlbumInfo, Library, SeriesInfo
from bdnex.ui import reset_cmd
from helpers import ALBUM_URL, FakeWeb
from importer.test_session import Env, make_settings


def filled(lib, make_cbz):
    album = lib.add_album(AlbumInfo("b", "1", "Redwin", series=SeriesInfo("b", "s", "Nains"), volume=1,
                                    publisher="Soleil", credits={"Writer": ["Jarry"]}, genres=["Fantasy"]))
    lib.edit_album(album, {"title": "Mine"})
    lib.add_file(make_cbz("A.cbz"), album)
    lib.log_import(make_cbz("A.cbz"), "tagged")
    lib.cache_put("b", "sitemap", "https://x/albums_1.xml", b"<urlset/>")
    lib.cache_put("b", "album", "https://x/BD-1.html", b"<html>page</html>")
    lib.cache_put("b", "cover", "https://x/c.jpg", b"JPEG")
    return album


def test_reset_empties_the_collection_and_keeps_the_downloads(make_cbz):
    lib = Library(":memory:")
    filled(lib, make_cbz)
    counts = lib.reset(apply=False)
    assert counts["albums"] == 1 and counts["files"] == 1 and counts["people"] == 1 and lib.stats()["albums"] == 1
    lib.reset()
    assert lib.stats() == {k: 0 for k in lib.stats()}
    for table in ("genres", "publishers", "album_credits", "album_genres", "import_log"):
        assert lib.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert lib.cache_get("b", "sitemap", "https://x/albums_1.xml") == b"<urlset/>"
    assert lib.cache_get("b", "album", "https://x/BD-1.html") == b"<html>page</html>"
    assert lib.cache_get("b", "cover", "https://x/c.jpg") == b"JPEG"
    assert lib.schema_version == SCHEMA_VERSION and lib.conn.execute("SELECT value FROM meta").fetchone()[0] == "relative"


def test_the_library_is_usable_after_a_reset(make_cbz):
    lib = Library(":memory:")
    filled(lib, make_cbz)
    lib.reset()
    album = lib.add_album(AlbumInfo("b", "1", "Redwin", series=SeriesInfo("b", "s", "Nains"), volume=1))
    assert lib.get_album(album)["title"] == "Redwin" and "edits" not in lib.get_album(album)["extra"]


def test_cache_summary(make_cbz):
    lib = Library(":memory:")
    filled(lib, make_cbz)
    assert lib.cache_summary() == {"sitemap": (1, 9), "album": (1, 17), "cover": (1, 4)}


def test_backup_is_a_full_copy(make_cbz, tmp_path):
    lib = Library(":memory:")
    filled(lib, make_cbz)
    copy = lib.backup_to(tmp_path / "copy.sqlite")
    conn = sqlite3.connect(copy)
    assert conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM source_cache").fetchone()[0] == 3


def test_importing_again_after_a_reset_needs_no_network(make_cbz):
    web = FakeWeb()
    env = Env(web, make_settings(), None)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: type(env.source).album_info(env.source, ALBUM_URL, recache)
    path = make_cbz("Nains - T01 - Redwin de la forge.cbz")
    assert env.session.run([path]).results[0].outcome == "tagged"
    assert ALBUM_URL in web.calls
    env.lib.reset()
    web.calls.clear()
    result = env.session.run([path]).results[0]
    assert result.outcome == "tagged" and web.calls == []  # the page and the sitemaps came from the library


# ------------------------------------------------------------------- command
@pytest.fixture
def cmd_env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(db) as lib:
        filled(lib, make_cbz)
    return db


class Answer:
    def __init__(self, value):
        self.value, self.asked = value, []

    def confirm(self, message):
        self.asked.append(message)
        return self.value


def reset(argv, answer=True):
    out, prompter = io.StringIO(), Answer(answer)
    return reset_cmd.run(argv, out=out, prompter=prompter), out.getvalue(), prompter


def count(db, table="albums"):
    return sqlite3.connect(db).execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_pretend_lists_what_goes_and_what_stays(cmd_env):
    code, text, prompter = reset(["-n"])
    assert code == 0 and "Would be deleted" in text and "1 albums" in text.replace("      ", " ").replace("  ", " ")
    assert "Kept: the downloaded data (1 album, 1 cover, 1 sitemap" in text and not prompter.asked
    assert count(cmd_env) == 1


def test_declining_changes_nothing(cmd_env):
    code, text, prompter = reset([], answer=False)
    assert code == 1 and prompter.asked and "Nothing changed" in text and count(cmd_env) == 1
    assert not glob.glob(str(cmd_env) + ".before-reset-*")


def test_reset_makes_a_backup_then_empties(cmd_env):
    code, text, _ = reset([])
    assert code == 0 and "Backup of the database" in text
    assert count(cmd_env) == 0 and count(cmd_env, "files") == 0 and count(cmd_env, "source_cache") == 3
    (backup,) = glob.glob(str(cmd_env) + ".before-reset-*")
    assert count(backup) == 1 and count(backup, "files") == 1  # the old state is recoverable


def test_yes_and_no_backup(cmd_env):
    code, text, prompter = reset(["-y", "--no-backup"])
    assert code == 0 and not prompter.asked and count(cmd_env) == 0 and not glob.glob(str(cmd_env) + ".before-reset-*")


def test_resetting_an_empty_library_says_so(cmd_env):
    reset(["-y", "--no-backup"])
    code, text, _ = reset([])
    assert code == 0 and "already empty" in text


def test_no_library_yet(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "none.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert reset_cmd.run([], out=io.StringIO()) == 1 and "nothing to reset" in capsys.readouterr().err
