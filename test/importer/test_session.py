import os

import pytest

from bdnex.config import build_settings
from bdnex.importer.bedetheque import Bedetheque
from bdnex.importer.bedetheque.bedetheque import BlockedError
from bdnex.importer.matching import (Recommendation, query_from_filename, recommend, rerank,
                                     slug_album_info, volumes_in)
from bdnex.importer.bedetheque.sitemap import Candidate
from bdnex.importer.session import DUPLICATE, FAILED, PARTIAL, PRETEND, SKIPPED, TAGGED, ImportSession
from bdnex.library import Library
from helpers import ALBUM_URL, FakeWeb

WWW_URL = ALBUM_URL.replace("m.bedetheque", "www.bedetheque")
FILE = "Nains - T01 - Redwin de la forge.cbz"


class FakePrompter:
    def __init__(self, choice="first", dup="skip"):
        self.choice, self.dup = choice, dup
        self.asked = []

    def choose_match(self, path, matches):
        self.asked.append(("match", path))
        if self.choice == "first":
            return matches[0].candidate if matches else None
        return self.choice

    def duplicate_action(self, path, others, what):
        self.asked.append(("dup", what))
        return self.dup


def make_settings(**overrides):
    user = {"config_version": 1, **overrides}
    return build_settings(user)[0]


class Env:
    def __init__(self, web, settings, prompter=None, **kw):
        self.web = web
        self.lib = Library(":memory:")
        self.source = Bedetheque(self.lib, fetch=web, sleep=lambda s: None)
        self.session = ImportSession(self.lib, self.source, settings, prompter, **kw)


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def env(web):
    e = Env(web, make_settings(), FakePrompter())
    yield e
    e.lib.close()


# --------------------------------------------------------------- matching
def test_query_from_filename_normalises_volume():
    assert query_from_filename("/x/Nains - T01 - Redwin_de_la.forge.cbz") == "Nains 1 Redwin de la forge"
    assert query_from_filename("Astérix Tome 12.cbz") == "Astérix 12"


def test_volumes_in():
    assert volumes_in("Nains 1 Redwin") == [1]
    assert volumes_in("no number here") == []


def test_rerank_prefers_matching_volume():
    c1 = Candidate("1", "u1", "nains 1", volume=1)
    c2 = Candidate("2", "u2", "nains 2", volume=2)
    ranked = rerank("Nains 2", [(c1, 80.0), (c2, 78.0)])
    assert ranked[0].candidate is c2 and ranked[0].score == 83.0 and ranked[1].score == 65.0


def test_recommend_thresholds():
    assert recommend(95, 90, 70) is Recommendation.STRONG
    assert recommend(80, 90, 70) is Recommendation.MEDIUM
    assert recommend(10, 90, 70) is Recommendation.NONE


def test_slug_album_info_series_and_oneshot():
    s = slug_album_info(Candidate("245127", WWW_URL, "x", 1))
    assert (s.series.name, s.volume, s.title) == ("Nains", 1, "Redwin de la Forge")
    one = slug_album_info(Candidate("433432", "https://www.bedetheque.com/BD-Bibliomule-de-Cordoue-433432.html", "x"))
    assert one.kind == "oneshot" and one.title == "Bibliomule de Cordoue" and one.extra["partial"]


# ------------------------------------------------------------ the session
def test_strong_match_is_imported_with_full_metadata(env, make_cbz):
    path = make_cbz(FILE)
    env.source.update_sitemaps()
    # the fake web serves the legacy mobile page under the mobile url: point the candidate at it
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    result = env.session.import_file(path)
    assert result.outcome == TAGGED and result.score >= 90
    album = env.lib.get_album(result.album_id)
    assert album["series"] == "Nains" and album["credits"]["Writer"] == ["Jarry, Nicolas"]
    assert env.lib.get_file(path)["album_id"] == result.album_id
    assert env.lib.last_import(path)["outcome"] == TAGGED
    assert env.session.prompter.asked == []  # strong: no question


def test_run_downloads_sitemaps_once_and_reports(env, make_cbz, tmp_path):
    make_cbz(FILE)
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    report = env.session.run([str(tmp_path)])
    assert report.count(TAGGED) == 1 and env.source.sitemaps_cached() == 26
    n = len(env.web.calls)
    env.session.run([str(tmp_path)])  # second run: skipped, no new requests
    assert len(env.web.calls) == n


def test_already_imported_is_skipped_unless_forced(env, make_cbz):
    path = make_cbz(FILE)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    env.session.import_file(path)
    assert env.session.import_file(path).outcome == SKIPPED
    env.session.force = True
    assert env.session.import_file(path).outcome == TAGGED


def test_page_blocked_falls_back_to_sitemap_data(env, make_cbz):
    path = make_cbz(FILE)
    env.source.update_sitemaps()

    def blocked(url, recache=False):
        raise BlockedError("challenge")

    env.source.album_info = blocked
    result = env.session.import_file(path)
    assert result.outcome == PARTIAL and "save the page" in result.note
    album = env.lib.get_album(result.album_id)
    assert (album["series"], album["volume"], album["title"]) == ("Nains", 1, "Redwin de la Forge")
    assert album["extra"]["partial"] is True and album["credits"] == {}


def test_partial_album_is_upgraded_when_page_becomes_available(env, make_cbz):
    path = make_cbz(FILE)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: (_ for _ in ()).throw(FileNotFoundError("nope"))
    first = env.session.import_file(path)
    assert first.outcome == PARTIAL and env.lib.stats()["series"] == 1
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    env.session.force = True
    second = env.session.import_file(path)
    assert second.outcome == TAGGED and second.album_id == first.album_id
    assert env.lib.get_album(second.album_id)["credits"]["Writer"] == ["Jarry, Nicolas"]
    assert env.lib.stats()["series"] == 1  # slug placeholder series pruned


def test_weak_match_asks_the_prompter(web, make_cbz):
    env = Env(web, make_settings(match={"strong_threshold": 100}), FakePrompter("first"))
    path = make_cbz("Nains Redwin forge.cbz")
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    result = env.session.import_file(path)
    assert env.session.prompter.asked == [("match", path)]
    assert result.outcome == TAGGED


def test_user_can_skip_a_match(web, make_cbz):
    env = Env(web, make_settings(match={"strong_threshold": 100}), FakePrompter(choice=None))
    path = make_cbz("Nains Redwin forge.cbz")
    env.source.update_sitemaps()
    assert env.session.import_file(path).outcome == SKIPPED
    assert env.lib.get_file(path) is None


def test_force_applies_medium_but_skips_no_match(web, make_cbz):
    env = Env(web, make_settings(match={"strong_threshold": 100}), prompter=None, force=True)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    assert env.session.import_file(make_cbz("Nains Redwin forge.cbz")).outcome == TAGGED
    assert env.session.import_file(make_cbz("zzzzzzzzzz qqqqqqqq.cbz", pages=7)).outcome == SKIPPED


def test_pretend_changes_nothing(web, make_cbz):
    env = Env(web, make_settings(), FakePrompter(), pretend=True)
    path = make_cbz(FILE)
    env.source.update_sitemaps()
    calls = len(web.calls)
    result = env.session.import_file(path)
    assert result.outcome == PRETEND and "www.bedetheque.com" in result.note
    assert env.lib.stats()["albums"] == 0 and env.lib.get_file(path) is None
    assert env.lib.last_import(path) is None and len(web.calls) == calls


def test_corrupted_file_fails_without_stopping_the_batch(env, make_cbz, tmp_path):
    (tmp_path / "bad.cbz").write_bytes(b"not a zip")
    make_cbz(FILE)
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    report = env.session.run([str(tmp_path)])
    assert report.count(FAILED) == 1 and report.count(TAGGED) == 1


def test_identical_file_is_a_duplicate(env, make_cbz, tmp_path):
    a = make_cbz(FILE)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    env.session.import_file(a)
    copy = str(tmp_path / "copy.cbz")
    with open(a, "rb") as src, open(copy, "wb") as dst:
        dst.write(src.read())
    result = env.session.import_file(copy)
    assert result.outcome == DUPLICATE and "identical" in result.note
    assert env.session.prompter.asked == [("dup", "identical file")]
    assert env.lib.get_file(copy) is None


def test_same_album_other_file_duplicate_actions(web, make_cbz):
    for action, expected in (("skip", DUPLICATE), ("keep_both", TAGGED), ("replace", TAGGED)):
        env = Env(web, make_settings(duplicates={"action": action}), FakePrompter())
        env.source.update_sitemaps()
        env.source.album_info = lambda url, recache=False, e=env: Bedetheque.album_info(e.source, ALBUM_URL, recache)
        first = make_cbz(f"{action}-1 Nains - T01 - Redwin de la forge.cbz")
        second = make_cbz(f"{action}-2 Nains - T01 - Redwin de la forge.cbz", pages=4)
        r1 = env.session.import_file(first)
        r2 = env.session.import_file(second)
        assert r1.outcome == TAGGED and r2.outcome == expected, action
        if action == "replace":
            assert env.lib.get_file(first)["album_id"] is None
            assert env.lib.get_file(second)["album_id"] == r2.album_id
        if action == "keep_both":
            assert len(env.lib.files_for_album(r2.album_id)) == 2
        env.lib.close()


def test_album_duplicate_asks_when_action_is_ask(env, make_cbz):
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    env.session.import_file(make_cbz("a Nains - T01 - Redwin de la forge.cbz"))
    env.session.import_file(make_cbz("b Nains - T01 - Redwin de la forge.cbz", pages=5))
    assert ("dup", "album already linked") in env.session.prompter.asked
