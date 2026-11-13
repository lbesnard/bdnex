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


def test_each_cover_has_a_folder_of_its_own(tmp_path):
    assert covers.save_cover(tmp_path, "bedetheque", "12", b"\x89PNG....") == "bedetheque/12/cover.png"
    assert covers.find_cover(tmp_path, "bedetheque", "12") == "bedetheque/12/cover.png"
    odd = covers.save_cover(tmp_path, "bedetheque", "../x", b"JPEG")
    assert odd.startswith("bedetheque/") and odd.count("/") == 2 and not odd.split("/")[1].startswith(".")
    assert covers.save_cover(tmp_path, "bedetheque", "..x", b"JPEG") != odd  # a changed name never collides with another
    assert covers.cover_folder("bedetheque", "7") != covers.cover_folder("other", "7")


def test_covers_saved_by_earlier_versions_move_into_their_folders(tmp_path):
    env = env_for(web_with_cover(), check=False, download=True)
    path = cbz_with_cover(tmp_path, FILE, 1)
    env.session.run([path])
    album = env.lib.get_album(env.lib.get_file(path)["album_id"])
    root = env.session.settings.cover_path
    new = root / album["cover_file"]
    old = root / "bedetheque" / f"{album['source_id']}.jpg"  # where the earlier layout kept it
    os.replace(new, old)
    env.lib.set_cover_file(album["id"], f"bedetheque/{album['source_id']}.jpg")
    messages = []
    env.session.progress = messages.append
    env.session.run([path])
    again = env.lib.get_album(album["id"])
    assert again["cover_file"] == f"bedetheque/{album['source_id']}/cover.jpg"
    assert (root / again["cover_file"]).read_bytes() == jpeg(art(1)) and not old.exists()
    assert any("folder per album" in m for m in messages)
