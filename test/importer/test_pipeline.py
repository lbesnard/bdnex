"""Matching in the background, the review gate and the deferred page fetch."""

import os
import time

import pytest

from bdnex.config import ConfigError, build_settings
from bdnex.importer.matching import Match
from bdnex.importer.pipeline import can_fork, match_ahead, resolve_workers
from bdnex.importer.session import PARTIAL, TAGGED
from bdnex.importer.source import Candidate
from helpers import FakeWeb, ALBUM_URL
from .test_session import Env, FakePrompter, make_settings

pytestmark = pytest.mark.skipif(not can_fork(), reason="workers are forked")


class Recorder:
    """A source that notes in a file, from whichever process runs it, each name it matches."""

    def __init__(self, log, delay=0.0):
        self.log, self.delay = log, delay

    def candidates(self, query, limit=5):
        time.sleep(self.delay)
        with open(self.log, "a") as fh:
            fh.write(query + "\n")
        if "boom" in query:
            raise RuntimeError("bad file")
        return [(Candidate("1", "https://x/1", query.lower(), None), 100.0)]


def matched_files(log):
    """Distinct files seen so far (each file is looked up with several queries)."""
    words = set(log.read_text().split()) if log.exists() else set()
    return {w for w in words if w in ("a", "b") or (w.startswith("f") and w[1:].isdigit())}


def test_results_come_back_in_order_with_errors_as_values(tmp_path):
    source = Recorder(tmp_path / "log")
    paths = [f"/c/f{i}.cbz" for i in range(10)] + ["/c/boom.cbz"]
    out = list(match_ahead(source, paths, workers=3, lookahead=4, min_parallel=0))
    assert [p for p, _ in out] == paths
    assert all(isinstance(m[0], Match) for _, m in out[:-1])
    assert isinstance(out[-1][1], RuntimeError)


def test_matching_goes_on_while_the_caller_is_busy(tmp_path):
    log = tmp_path / "log"
    stream = match_ahead(Recorder(log), [f"/c/f{i}.cbz" for i in range(30)], workers=2, lookahead=6, min_parallel=0)
    next(stream)  # the "user" now stops reading for a while
    deadline = time.time() + 10
    while time.time() < deadline and (not log.exists() or len(matched_files(log)) < 6):
        time.sleep(0.05)
    done = len(matched_files(log))
    assert 6 <= done <= 8  # the window was filled without anyone asking, and no further
    stream.close()


def test_few_files_or_one_worker_match_in_this_process(tmp_path):
    log = tmp_path / "log"
    list(match_ahead(Recorder(log), ["/c/a.cbz", "/c/b.cbz"], workers=4))  # below min_parallel
    list(match_ahead(Recorder(log), [f"/c/f{i}.cbz" for i in range(20)], workers=1))
    assert len(matched_files(log)) == 22


def test_workers_setting_resolves(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    assert resolve_workers(0) == 7 and resolve_workers(1) == 1 and resolve_workers(3) == 3
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    assert resolve_workers(0) == 1


@pytest.mark.parametrize("option, value", [("workers", -1), ("lookahead", 0)])
def test_import_options_are_validated(option, value):
    with pytest.raises(ConfigError, match=option):
        build_settings({"config_version": 2, "import": {option: value}})


# ------------------------------------------------------------------ review gate
@pytest.fixture
def web():
    return FakeWeb()


def prepared(web, settings, prompter, make_cbz, **kw):
    env = Env(web, settings, prompter, **kw)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: type(env.source).album_info(env.source, ALBUM_URL, recache)
    return env, make_cbz("Nains - T01 - Redwin de la forge.cbz")


def test_without_auto_even_a_strong_match_is_confirmed_by_the_user(web, make_cbz):
    env, path = prepared(web, make_settings(**{"import": {"auto": False}}), FakePrompter("first"), make_cbz)
    env.session.run([path])
    assert env.session.prompter.asked == [("match", path)]


def test_auto_applies_strong_matches_without_asking(web, make_cbz):
    env, path = prepared(web, make_settings(), FakePrompter("first"), make_cbz)
    report = env.session.run([path])
    assert env.session.prompter.asked == [] and report.results[0].outcome == TAGGED


def test_auto_flag_overrides_the_setting(web, make_cbz):
    env, path = prepared(web, make_settings(**{"import": {"auto": False}}), FakePrompter("first"), make_cbz, auto=True)
    env.session.run([path])
    assert env.session.prompter.asked == []


def test_user_can_still_skip_in_review(web, make_cbz):
    env, path = prepared(web, make_settings(**{"import": {"auto": False}}), FakePrompter(None), make_cbz)
    assert env.session.run([path]).results[0].outcome == "skipped"


# ------------------------------------------------------------ deferred page fetch
def page_requests(web):
    return [u for u in web.calls if u == ALBUM_URL]


def test_pages_are_fetched_after_the_files_are_linked(web, make_cbz):
    env, path = prepared(web, make_settings(), None, make_cbz)
    order = []
    original_add_file = env.lib.add_file
    env.lib.add_file = lambda *a, **k: (order.append("link"), original_add_file(*a, **k))[1]
    original = env.source.album_info
    env.source.album_info = lambda *a, **k: (order.append("page"), original(*a, **k))[1]
    report = env.session.run([path])
    assert order == ["link", "page"] and report.results[0].outcome == TAGGED
    assert env.lib.get_album(report.results[0].album_id)["extra"].get("partial") is None


def test_match_only_links_from_the_sitemap_and_requests_no_page(web, make_cbz):
    env, path = prepared(web, make_settings(), None, make_cbz, steps=("match",))
    env.source.album_info = lambda *a, **k: pytest.fail("a page was requested")
    result = env.session.run([path]).results[0]
    assert result.outcome == PARTIAL and "--fetch" in result.note
    assert env.lib.get_file(path)["album_id"] == result.album_id


def test_interrupting_the_fetch_keeps_the_links(web, make_cbz):
    env, path = prepared(web, make_settings(), None, make_cbz)
    env.source.album_info = lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
    messages = []
    env.session.progress = messages.append
    result = env.session.run([path]).results[0]
    assert env.lib.get_file(path)["album_id"] == result.album_id
    assert any("Interrupted" in m for m in messages)


def test_matched_albums_are_completed_later_by_fetching(web, make_cbz):
    env, path = prepared(web, make_settings(), None, make_cbz, steps=("match",))
    env.session.run([path])
    env.session.steps = frozenset(["fetch"])
    assert env.session.run([path]).results[0].outcome == TAGGED


def test_pretend_never_asks(web, make_cbz):
    env, path = prepared(web, make_settings(**{"import": {"auto": False}}), FakePrompter("first"), make_cbz, pretend=True)
    result = env.session.run([path]).results[0]
    assert env.session.prompter.asked == [] and result.outcome == "pretend"
