"""Covers are kept as files beside the library and linked from the album."""

import os

from bdnex.importer.source import Candidate

from bdnex.importer.bedetheque import Bedetheque
from helpers import ALBUM_URL, FakeWeb

from .test_cover_check import COVER_URL, FILE, art, cbz_with_cover, env_for, jpeg
from bdnex.importer import covers


def web_with_cover():
    web = FakeWeb()
    web.pages[COVER_URL] = jpeg(art(1))
    return web


def test_the_cover_is_saved_when_the_page_is_fetched(tmp_path):
    env = env_for(web_with_cover(), check=False, download=True)
    path = cbz_with_cover(tmp_path, FILE, 1)
    env.session.run([path])
    album = env.lib.get_album(env.lib.get_file(path)["album_id"])
    kept = env.session.settings.cover_path / album["cover_file"]
    assert album["cover_file"].startswith("bedetheque/") and kept.read_bytes() == jpeg(art(1))
    assert album["cover_url"] == COVER_URL  # the address it came from is still there


def test_a_missing_cover_is_downloaded_later_and_only_once(tmp_path):
    web = web_with_cover()
    env = env_for(web, check=False, download=False)
    path = cbz_with_cover(tmp_path, FILE, 1)
    env.session.run([path])
    assert env.lib.get_album(env.lib.get_file(path)["album_id"])["cover_file"] is None
    env.session.settings.cover.download, env.session.steps = True, frozenset(["fetch"])
    web.calls.clear()
    assert env.session.run([path]).results[0].outcome == "already" and COVER_URL in web.calls
    assert env.lib.get_album(env.lib.get_file(path)["album_id"])["cover_file"]
    web.calls.clear()
    env.session.run([path])
    assert COVER_URL not in web.calls  # the file is there: no second request


def test_a_cover_file_that_was_deleted_is_downloaded_again(tmp_path):
    web = web_with_cover()
    env = env_for(web, check=False, download=True)
    path = cbz_with_cover(tmp_path, FILE, 1)
    env.session.run([path])
    album = env.lib.get_album(env.lib.get_file(path)["album_id"])
    os.remove(env.session.settings.cover_path / album["cover_file"])
    env.session.steps = frozenset(["fetch"])
    web.calls.clear()
    env.session.run([path])
    assert COVER_URL in web.calls and (env.session.settings.cover_path / album["cover_file"]).exists()


def test_a_cover_file_survives_a_reset_and_is_reused(tmp_path):
    web = web_with_cover()
    env = env_for(web, check=False, download=True)
    path = cbz_with_cover(tmp_path, FILE, 1)
    env.session.run([path])
    env.lib.reset()
    web.calls.clear()
    env.session.run([path])
    assert COVER_URL not in web.calls
    assert env.lib.get_album(env.lib.get_file(path)["album_id"])["cover_file"]


def test_save_cover_names_the_file_by_its_content(tmp_path):
    assert covers.save_cover(tmp_path, "bedetheque", "12", b"\x89PNG....") == "bedetheque/12.png"
    assert covers.save_cover(tmp_path, "bedetheque", "../x", b"JPEG") == "bedetheque/.._x.jpg"
    assert covers.find_cover(tmp_path, "bedetheque", "12") == "bedetheque/12.png"


def test_interrupting_the_page_loop_does_not_start_a_cover_pass(tmp_path, monkeypatch):
    web = web_with_cover()
    env = env_for(web, check=False, download=True)
    path = cbz_with_cover(tmp_path, FILE, 1)
    env.session.run([path])
    os.remove(next((env.session.settings.cover_path / "bedetheque").iterdir()))  # a complete album lacking its cover
    web.calls.clear()
    env.session.steps = frozenset(["fetch"])
    env.session._covers = [object()]  # work waiting for the cover pass
    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(env.session, "_album_for", interrupted)
    env.session._pending = [(type("R", (), {"note": "", "path": path, "score": None})(), Candidate("1", "https://www.bedetheque.com/BD-X-1.html", "x"), "")]
    env.session._fetch_pending()
    assert env.session._covers == [] and COVER_URL not in web.calls
