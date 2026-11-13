"""``cover.check``: compare a file's cover with its album's, keep the score, ask when they differ."""

import sys
import zipfile

import pytest

pytest.importorskip("cv2")

from bdnex.importer.session import ALREADY, PARTIAL, SKIPPED, TAGGED
from helpers import ALBUM_URL, FakeWeb

from .test_covers import art, jpeg
from .test_session import FILE, Bedetheque, Env, make_settings

COVER_URL = "https://www.bedetheque.com/media/Couvertures/Couv_245127.jpg"


class Prompter:
    def __init__(self, answer="keep"):
        self.answer, self.asked = answer, []

    def choose_match(self, path, matches):
        return matches[0].candidate if matches else None

    def cover_mismatch(self, path, album, score, threshold):
        self.asked.append((album, score, threshold))
        return self.answer


def cbz_with_cover(tmp_path, name, seed):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("page-1.jpg", jpeg(art(seed)))
        archive.writestr("page-2.jpg", jpeg(art(99)))
    return str(path)


def env_for(web, prompter=None, check=True, download=False, **kw):
    env = Env(web, make_settings(cover={"check": check, "download": download}), prompter, **kw)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    return env


def web_with_cover(seed):
    web = FakeWeb()
    web.pages[COVER_URL] = jpeg(art(seed))
    return web


def test_a_cover_that_matches_is_scored_and_causes_no_question(tmp_path):
    web = web_with_cover(1)
    env = env_for(web, Prompter())
    result = env.session.run([cbz_with_cover(tmp_path, FILE, 1)]).results[0]
    assert result.outcome == TAGGED and result.note == "" and env.session.prompter.asked == []
    assert env.lib.get_file(str(tmp_path / FILE))["cover_score"] > 40 and COVER_URL in web.calls


def test_another_cover_asks_and_unlinking_forgets_the_match(tmp_path):
    env = env_for(web_with_cover(1), Prompter("unlink"))
    path = cbz_with_cover(tmp_path, FILE, 2)
    result = env.session.run([path]).results[0]
    (album, score, threshold), = env.session.prompter.asked
    assert threshold == 40 and score < 40 and "Nains" in album
    assert result.outcome == SKIPPED and "unlinked" in result.note
    row = env.lib.get_file(path)
    assert row["album_id"] is None and row["cover_score"] == score
    assert env.lib.last_import(path)["outcome"] == "skipped"


def test_keeping_a_doubtful_match_flags_it_and_keeps_the_score(tmp_path):
    env = env_for(web_with_cover(1), Prompter("keep"))
    path = cbz_with_cover(tmp_path, FILE, 2)
    result = env.session.run([path]).results[0]
    assert result.outcome == TAGGED and "the cover differs" in result.note
    assert env.lib.get_file(path)["album_id"] is not None and env.lib.get_file(path)["cover_score"] < 40
    assert [r["path"] for r in env.lib.query(["cover:..40"], files=True)] == [path]
    assert env.lib.get_file(path)["review"] is None  # you looked at it and kept it: nothing to review


def test_an_unattended_run_flags_the_doubtful_match_for_review(tmp_path):
    env = env_for(web_with_cover(1), None)
    path = cbz_with_cover(tmp_path, FILE, 2)
    result = env.session.run([path]).results[0]
    assert "`bdnex review`" in result.note
    assert "cover differs" in env.lib.get_file(path)["review"]
    assert [r["path"] for r in env.lib.query(["review:true"], files=True)] == [path]
    env.lib.set_file_album(path, None)
    assert env.lib.get_file(path)["review"] is None  # unlinked: no longer a match to question


def test_unattended_runs_keep_the_match_and_say_so(tmp_path):
    env = env_for(web_with_cover(1), None)
    result = env.session.run([cbz_with_cover(tmp_path, FILE, 2)]).results[0]
    assert result.outcome == TAGGED and "the cover differs" in result.note


def test_nothing_is_requested_or_computed_when_the_check_is_off(tmp_path):
    web = web_with_cover(1)
    env = env_for(web, Prompter(), check=False)
    env.session.run([cbz_with_cover(tmp_path, FILE, 2)])
    assert COVER_URL not in web.calls and env.lib.get_file(str(tmp_path / FILE))["cover_score"] is None


def test_the_flag_turns_it_on_for_one_run(tmp_path):
    web = web_with_cover(1)
    env = env_for(web, Prompter(), check=False, cover_check=True)
    env.session.run([cbz_with_cover(tmp_path, FILE, 1)])
    assert COVER_URL in web.calls


def test_a_cover_that_is_not_an_image_is_noted_not_fatal(tmp_path):
    web = FakeWeb()
    web.pages[COVER_URL] = b"<html><body><img src='x'></body></html>"  # what a browser-based fetcher hands back
    env = env_for(web, Prompter())
    result = env.session.run([cbz_with_cover(tmp_path, FILE, 1)]).results[0]
    assert result.outcome == TAGGED and "cover not downloaded" in result.note and "not an image" in result.note


def test_without_opencv_the_import_goes_on_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    env = env_for(web_with_cover(1), Prompter())
    messages = []
    env.session.progress = messages.append
    result = env.session.run([cbz_with_cover(tmp_path, FILE, 1)]).results[0]
    assert result.outcome == TAGGED and any("bdnex[cover]" in m for m in messages)


def test_albums_imported_before_get_their_cover_compared_once(tmp_path):
    web = web_with_cover(1)
    env = env_for(web, Prompter(), check=False)
    path = cbz_with_cover(tmp_path, FILE, 1)
    assert env.session.run([path]).results[0].outcome == TAGGED
    assert env.lib.get_file(path)["cover_score"] is None
    env.session.cover_check, env.session.steps = True, frozenset(["fetch"])
    web.calls.clear()
    result = env.session.run([path]).results[0]
    assert result.outcome == ALREADY and env.lib.get_file(path)["cover_score"] > 40 and COVER_URL in web.calls
    web.calls.clear()
    assert env.session.run([path]).results[0].outcome == ALREADY and COVER_URL not in web.calls  # not again


def test_a_preview_checks_nothing(tmp_path):
    web = web_with_cover(1)
    env = env_for(web, Prompter(), pretend=True)
    env.session.run([cbz_with_cover(tmp_path, FILE, 2)])
    assert COVER_URL not in web.calls
