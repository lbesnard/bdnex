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


def test_a_cover_is_downloaded_in_the_background_as_soon_as_the_album_is_complete(tmp_path):
    import threading

    env = env_for(web_with_cover(), check=True, download=True)
    seen = []
    original = env.source.fetch_cover
    env.source.fetch_cover = lambda url: seen.append(threading.current_thread().name) or original(url)
    path = cbz_with_cover(tmp_path, FILE, 1)
    env.session.run([path])
    row = env.lib.get_file(path)
    assert seen and seen[0].startswith("bdnex-cover")  # not the main thread: nothing waited for it
    assert row["cover_score"] is not None and env.lib.get_album(row["album_id"])["cover_file"]


def test_a_cover_that_cannot_be_downloaded_stops_nothing(tmp_path):
    env = env_for(FakeWeb(), check=True, download=True)  # no picture at COVER_URL
    env.source.fetch_cover = lambda url: (_ for _ in ()).throw(env.source.fetch_errors[0]("no cover"))
    path = cbz_with_cover(tmp_path, FILE, 1)
    result = env.session.run([path]).results[0]
    assert result.outcome == "tagged" and "cover not downloaded" in result.note
    assert env.lib.get_file(path)["cover_score"] is None  # the next `import --fetch` tries again


def test_fetch_gets_the_missing_covers_of_albums_already_complete_in_the_background(tmp_path):
    import threading

    web = web_with_cover()
    env = env_for(web, check=False, download=False)
    path = cbz_with_cover(tmp_path, FILE, 1)
    env.session.run([path])  # complete, but no cover kept and none compared
    assert env.lib.get_file(path)["cover_score"] is None
    env.session.settings.cover.download, env.session.steps = True, frozenset(["fetch"])
    env.session.cover_check = True
    seen, original = [], env.source.fetch_cover
    env.source.fetch_cover = lambda url: seen.append(threading.current_thread().name) or original(url)
    env.session.run([path])
    assert seen[0].startswith("bdnex-cover") and env.lib.get_file(path)["cover_score"] is not None


def test_the_plain_import_also_downloads_the_missing_covers_of_albums_already_done(tmp_path):
    web = web_with_cover()
    env = env_for(web, check=False, download=False)
    path = cbz_with_cover(tmp_path, FILE, 1)
    env.session.run([path])
    env.session.settings.cover.download = True  # both steps, as `import --database-only` runs them
    web.calls.clear()
    result = env.session.run([path]).results[0]
    assert result.outcome == "already" and COVER_URL in web.calls
    assert env.lib.get_album(env.lib.get_file(path)["album_id"])["cover_file"]


def test_stopping_gives_up_the_requests_not_yet_sent(tmp_path):
    from bdnex.importer.bedetheque.bedetheque import RequestsStopped

    env = env_for(web_with_cover(), check=True, download=True)
    env.source.stop_requests()
    try:
        env.source.fetch_cover(COVER_URL)
        raise AssertionError("the request was sent")
    except RequestsStopped:
        pass
    env.source.allow_requests()
    assert env.source.fetch_cover(COVER_URL)  # a new run sends requests again


# ---------------------------------------------------------------- the cover votes for the files nobody decided
def _vote_env(tmp_path, file_seed, album_seed, other_seed=None, **match):
    """A file whose best match is only medium; `--auto=all` leaves it undecided; the covers vote."""
    web = FakeWeb()
    web.pages[COVER_URL] = jpeg(art(album_seed))
    env = env_for(web, check=True, download=True)
    env.session.settings.match.strong_threshold = 100  # the match is never strong
    for key, value in match.items():
        setattr(env.session.settings.match, key, value)
    env.session.auto = "all"
    return env, web, cbz_with_cover(tmp_path, "Nains Redwin.cbz", file_seed)  # a medium match, not a strong one


def test_a_cover_that_looks_like_the_files_decides_a_medium_match_nobody_applied(tmp_path):
    env, web, path = _vote_env(tmp_path, file_seed=1, album_seed=1)
    report = env.session.run([path])
    result = report.results[0]
    assert result.outcome == "tagged" and env.lib.get_file(path)["album_id"] is not None
    assert env.lib.last_import(path)["outcome"] == "tagged"


def test_a_cover_that_differs_leaves_the_file_skipped(tmp_path):
    env, web, path = _vote_env(tmp_path, file_seed=1, album_seed=2)
    report = env.session.run([path])
    assert report.results[0].outcome == "skipped" and env.lib.last_import(path)["outcome"] == "skipped"


def test_the_vote_can_be_turned_off(tmp_path):
    env, web, path = _vote_env(tmp_path, file_seed=1, album_seed=1, cover_vote=False)
    assert env.session.run([path]).results[0].outcome == "skipped"
    assert COVER_URL not in web.calls  # nothing was requested for it


def test_a_vote_needs_a_clear_winner_and_a_good_enough_cover(tmp_path):
    from types import SimpleNamespace

    env, web, path = _vote_env(tmp_path, file_seed=1, album_seed=1)
    env.session.run([path])  # the vote decided it; now ask the vote itself about two candidates with the same cover
    candidates = env.source.candidates("Nains Redwin", limit=2)
    matches = [SimpleNamespace(candidate=c, score=80.0) for c, _ in candidates]
    env.session.settings.match.cover_vote_min = 100
    covers_by_id = {m.candidate.source_id: jpeg(art(1)) for m in matches}
    env.session._candidate_cover = lambda candidate, fetched: covers_by_id[candidate.source_id]
    env.session._cover_urls = lambda pool: {}  # nothing to request: the covers are given
    if len(matches) > 1:
        assert env.session._vote(path, matches) is None  # two identical covers: no clear winner
