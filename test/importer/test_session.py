import os

import pytest

from bdnex.config import build_settings
from bdnex.importer.bedetheque import Bedetheque
from bdnex.importer.bedetheque.bedetheque import BlockedError
from bdnex.importer.bedetheque.parser import partial_album_info as slug_album_info
from bdnex.importer.matching import Recommendation, query_from_filename, recommend, rerank, volumes_in
from bdnex.importer.source import Candidate
from bdnex.importer.session import ALREADY, DUPLICATE, FAILED, PARTIAL, PRETEND, SKIPPED, TAGGED, ImportSession
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
    user = {"config_version": 2, "import": {"auto": True}, "cover": {"check": False}, **overrides}
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
    assert env.session.import_file(path).outcome == ALREADY
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
    path = make_cbz("Nains Redwin.cbz")
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    result = env.session.import_file(path)
    assert env.session.prompter.asked == [("match", path)]
    assert result.outcome == TAGGED


def test_user_can_skip_a_match(web, make_cbz):
    env = Env(web, make_settings(match={"strong_threshold": 100}), FakePrompter(choice=None))
    path = make_cbz("Nains Redwin.cbz")
    env.source.update_sitemaps()
    assert env.session.import_file(path).outcome == SKIPPED
    assert env.lib.get_file(path) is None


def test_force_applies_medium_but_skips_no_match(web, make_cbz):
    env = Env(web, make_settings(match={"strong_threshold": 100}), prompter=None, auto="all")
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
        if action == "replace":  # the old file goes to the trash: gone from the library and from its place
            assert env.lib.get_file(first) is None and not os.path.exists(first)
            (entry,) = env.lib.trash_entries()
            assert entry["path"] == first and entry["album_id"] == r2.album_id and os.path.exists(entry["trash_path"])
            assert env.lib.get_file(second)["album_id"] == r2.album_id and "trash" in r2.note
        if action == "keep_both":  # the new file is a spare version of the album
            assert len(env.lib.files_for_album(r2.album_id)) == 2
            assert env.lib.get_file(first)["spare"] == 0 and env.lib.get_file(second)["spare"] == 1
        env.lib.close()


def test_album_duplicate_asks_when_action_is_ask(env, make_cbz):
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    env.session.import_file(make_cbz("a Nains - T01 - Redwin de la forge.cbz"))
    env.session.import_file(make_cbz("b Nains - T01 - Redwin de la forge.cbz", pages=5))
    assert ("dup", "album already linked") in env.session.prompter.asked


def test_duplicate_keys_catch_another_edition_of_the_same_tome(web, make_cbz):
    """Different source ids, same series + volume: a duplicate according to duplicates.keys."""
    from bdnex.library import AlbumInfo, SeriesInfo
    env = Env(web, make_settings(duplicates={"action": "skip"}), FakePrompter())
    env.source.update_sitemaps()
    other = env.lib.add_album(AlbumInfo("bedetheque", "999999", "Autre édition",
                                        series=SeriesInfo("bedetheque", "5", "Nains"), volume=1))
    env.lib.add_file(make_cbz("other-edition.cbz", pages=9), other)
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    result = env.session.import_file(make_cbz(FILE))
    assert result.outcome == DUPLICATE
    env.lib.close()


def test_duplicate_keys_can_be_narrowed_to_the_title(web, make_cbz):
    from bdnex.library import AlbumInfo, SeriesInfo
    env = Env(web, make_settings(duplicates={"action": "skip", "keys": ["title"]}), FakePrompter())
    env.source.update_sitemaps()
    other = env.lib.add_album(AlbumInfo("bedetheque", "999999", "Autre édition",
                                        series=SeriesInfo("bedetheque", "5", "Nains"), volume=1))
    env.lib.add_file(make_cbz("other-edition.cbz", pages=9), other)
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    assert env.session.import_file(make_cbz(FILE)).outcome == TAGGED  # title differs: not a duplicate
    env.lib.close()


def test_one_shots_are_never_duplicates_of_each_other(web, make_cbz):
    from bdnex.library import AlbumInfo
    env = Env(web, make_settings(duplicates={"action": "skip"}), FakePrompter())
    env.source.update_sitemaps()
    other = env.lib.add_album(AlbumInfo("bedetheque", "777", "Un autre one-shot"))
    env.lib.add_file(make_cbz("other.cbz", pages=9), other)
    assert env.lib.files_matching_album("bedetheque", "433432", {}, "/x") == []
    env.lib.close()


def test_nothing_to_import_makes_no_network_access(env, tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "notes.txt").write_text("x")
    report = env.session.run([str(tmp_path / "empty"), str(tmp_path / "missing.cbz")])
    assert env.web.calls == [] and env.lib.stats()["files"] == 0
    assert [r.outcome for r in report.results] == [FAILED] and "is not an existing file or folder" in report.results[0].note


def test_session_reports_progress(web, make_cbz, tmp_path):
    messages = []
    env = Env(web, make_settings(), FakePrompter(), progress=messages.append)
    make_cbz(FILE)
    make_cbz("zzzzzzzz qqqqqqqq.cbz", pages=7)
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    env.session.run([str(tmp_path)])
    assert messages[0].startswith("Preparing the bedetheque source")
    assert [m for m in messages if m.startswith("[") and not m.startswith("[page")] == ["[1/2] Nains - T01 - Redwin de la forge.cbz",
                                                         "[2/2] zzzzzzzz qqqqqqqq.cbz"]
    env.lib.close()


# ------------------------------------------------------------- --retry-partial
def unavailable(url, recache=False):
    raise FileNotFoundError("no saved page")


def partial_env(web, make_cbz, **kw):
    env = Env(web, make_settings(), FakePrompter(), **kw)
    env.source.update_sitemaps()
    env.source.album_info = unavailable
    path = make_cbz(FILE)
    first = env.session.import_file(path)
    assert first.outcome == PARTIAL
    return env, path, first


def test_matched_only_file_says_how_to_complete_it_when_fetching_is_not_asked(web, make_cbz):
    env, path, first = partial_env(web, make_cbz)
    env.session.steps = frozenset(["match"])
    result = env.session.import_file(path)
    assert result.outcome == ALREADY and "--fetch" in result.note
    env.lib.close()


def test_importing_again_fetches_the_missing_page_without_matching_again(web, make_cbz):
    env, path, first = partial_env(web, make_cbz)
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    matched = []
    env.source.candidates = lambda *a, **k: matched.append(1) or []
    result = env.session.import_file(path)
    assert result.outcome == TAGGED and result.album_id == first.album_id and matched == []  # no new matching
    assert env.lib.get_album(first.album_id)["credits"]["Writer"] == ["Jarry, Nicolas"]
    assert env.session.prompter.asked == []
    assert env.session.import_file(path).outcome == ALREADY  # complete now: nothing left to do
    env.lib.close()


def test_missing_page_stays_matched_when_it_is_still_unavailable(web, make_cbz):
    env, path, first = partial_env(web, make_cbz)
    result = env.session.import_file(path)
    assert result.outcome == PARTIAL and result.album_id == first.album_id and "save the page" in result.note
    env.lib.close()


def test_fetching_leaves_complete_albums_alone(web, make_cbz):
    env = Env(web, make_settings(), FakePrompter())
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    path = make_cbz(FILE)
    assert env.session.import_file(path).outcome == TAGGED
    env.session.steps = frozenset(['fetch'])
    calls = len(web.calls)
    assert env.session.import_file(path).outcome == ALREADY and len(web.calls) == calls
    env.lib.close()


def test_fetch_pretend_changes_nothing(web, make_cbz):
    env, path, first = partial_env(web, make_cbz)
    env.session.pretend = True
    calls = len(web.calls)
    result = env.session.import_file(path)
    assert result.outcome == PRETEND and "www.bedetheque.com" in result.note and len(web.calls) == calls
    env.lib.close()


def test_force_still_redoes_everything(web, make_cbz):
    env, path, first = partial_env(web, make_cbz)
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    env.session.force = True
    assert env.session.import_file(path).outcome == TAGGED
    env.lib.close()


@pytest.mark.parametrize("name, expected", [
    ("La Bibliomule De Cordoue (Wilfrid Lupano) [2021].cbz", "La Bibliomule De Cordoue"),
    ("Nains - T01 - Redwin (Jarry) [2015].cbz", "Nains 1 Redwin"),
    ("Astérix (T3).cbz", "Astérix 3"),
    ("Astérix [12] - Le tour.cbz", "Astérix 12 Le tour"),
    ("Astérix (Tome 3) (Goscinny).cbz", "Astérix 3"),
    ("(Goscinny) [1961].cbz", "(Goscinny) [1961]"),  # nothing left otherwise: keep the name as it is
    ("Plain name.cbz", "Plain name"),
])
def test_query_ignores_author_and_year_groups(name, expected):
    assert query_from_filename("/x/" + name) == expected


def test_a_title_with_author_and_year_in_its_file_name_is_now_a_strong_match():
    from rapidfuzz import fuzz
    from bdnex.importer.bedetheque.sitemap import normalise_slug, remove_common_words
    query = query_from_filename("/x/La Bibliomule De Cordoue (Wilfrid Lupano) [2021].cbz")
    slug = normalise_slug("https://www.bedetheque.com/BD-Bibliomule-de-Cordoue-433432.html")
    assert fuzz.ratio(remove_common_words(query), slug) == 100.0


def test_progress_shows_the_match_and_the_page_fetch(web, make_cbz):
    messages = []
    env = Env(web, make_settings(), FakePrompter(), progress=messages.append)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    env.session.import_file(make_cbz(FILE))
    text = "\n".join(messages)
    assert "    match: BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html (score " in text
    assert "fetching the album page (bedetheque;" in text and "page read in " in text
    env.lib.close()


def test_progress_says_when_the_page_is_unavailable(web, make_cbz):
    messages = []
    env = Env(web, make_settings(), FakePrompter(), progress=messages.append)
    env.source.update_sitemaps()

    def no_page(url, recache=False):
        raise FileNotFoundError("no saved page")

    env.source.album_info = no_page
    env.session.import_file(make_cbz(FILE))
    assert any(m.startswith("    page unavailable after ") and "no saved page" in m for m in messages)
    env.lib.close()


def test_a_missing_argument_is_reported_as_typed(env, tmp_path):
    stray = "\u2502"  # the box-drawing character a terminal can paste by accident
    report = env.session.run([stray])
    assert report.results[0].path == stray and repr(stray) in report.results[0].note
    assert env.web.calls == []


def test_pages_are_fetched_several_at_a_time_when_the_source_says_so(web, make_cbz):
    env, path, first = partial_env(web, make_cbz)
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    env.source.parallel = 3
    web.pages[env.lib.get_album(first.album_id)["url"]] = web.pages[ALBUM_URL]  # the page the sitemap points at
    seen = []
    real = env.source.prefetch
    env.source.prefetch = lambda urls, workers, recache=False, **kw: seen.append((list(urls), workers)) or real(urls, workers, recache, **kw)
    messages = []
    env.session.progress = messages.append
    env.session.steps = frozenset(["fetch"])
    assert env.session.run([path]).results[0].outcome == TAGGED
    assert seen and seen[0][1] == 3 and any("3 pages at a time" in m for m in messages)
    env.lib.close()


def test_a_page_that_the_endpoints_could_not_fetch_is_reported_for_its_file(web, make_cbz):
    env, path, first = partial_env(web, make_cbz)
    env.source.parallel = 2
    env.source.prefetch = lambda urls, workers, recache=False, **kw: iter([(urls[0], OSError("every endpoint is down"))])
    env.session.steps = frozenset(["fetch"])
    result = env.session.run([path]).results[0]
    assert result.outcome == PARTIAL and "page not fetched: every endpoint is down" in result.note
    env.lib.close()
