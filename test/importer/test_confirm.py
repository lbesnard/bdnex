"""A second look at a match: credits, year, volume and the file's own tags against the album."""

import pytest

from bdnex.importer import confirm
from bdnex.importer.confirm import CONFIRMED, CONTRADICTED, DOUBTFUL, UNVERIFIED


def album(**kw):
    base = {"title": "Les pirates", "series": "Tiger Joe", "volume": 8, "year": 1985, "publisher": "Dupuis",
            "credits": {"Writer": ["Dupont, Marc"], "Penciller": ["Durand, Luc"]}}
    return {**base, **kw}


def test_the_volume_alone_confirms_nothing_since_the_match_was_ranked_on_it():
    ev = confirm.assess("/c/Tiger Joe - T08.cbz", album(credits={}, year=None))
    assert ev.verdict == UNVERIFIED and ev.agree == ["tome 8"]


def test_an_author_and_the_year_confirm_a_match():
    ev = confirm.assess("/c/Tiger Joe - T08 (Dupont) [1985].cbz", album())
    assert ev.verdict == CONFIRMED and {"tome 8", "the year 1985"} <= set(ev.agree) and not ev.reason


def test_nothing_to_compare_is_unverified_not_confirmed():
    assert confirm.assess("/c/Un livre.cbz", album(volume=None, year=None, credits={})).verdict == UNVERIFIED


def test_another_volume_with_another_title_is_contradicted():
    ev = confirm.assess("/c/Tiger Joe - T03.cbz", album(volume=8, title="Bengale"))
    assert ev.verdict == CONTRADICTED and "tome 3" in ev.reason


def test_another_volume_but_the_title_is_in_the_file_name_is_only_another_numbering():
    ev = confirm.assess("/c/Tiger Joe - T08 - Les pirates du Bengale.cbz", album(volume=85, title="Les pirates du Bengale"))
    assert ev.verdict != CONTRADICTED and "another numbering" in ev.reason


def test_a_special_issue_matched_to_a_numbered_tome():
    ev = confirm.assess("/c/Cités Obscures - Supplément.cbz", album(title="Babel", series="Cités", volume=11))
    assert ev.verdict == CONTRADICTED and "supplément" in ev.reason
    # an "intégrale" with a number is a number, not a special issue
    assert confirm.assess("/c/Dan Cooper - L'Intégrale - T11.cbz", album(volume=11)).verdict == UNVERIFIED


def test_the_files_own_tags_naming_another_series_contradict_the_match():
    ev = confirm.assess("/c/La Valise.cbz", album(series=None, title="Vogue la valise", volume=None),
                        {"Series": "PendientesC", "Year": "2018"})
    assert ev.verdict == CONTRADICTED and "PendientesC" in ev.reason


def test_a_series_name_inside_a_longer_one_is_the_same_series():
    ev = confirm.assess("/c/Demon Slayer T01.cbz", album(series="Demon Slayer - Kimetsu no yaiba", title="x", volume=1),
                        {"Series": "Demon Slayer", "Number": "1"})
    assert ev.verdict == CONFIRMED and "its own tags" in ev.agree


def test_two_weak_signals_make_it_doubtful_one_does_not():
    one_shot = album(series=None, volume=None, title="Even If", year=2012)
    year_only = confirm.assess("/c/Even (2021).cbz", one_shot)
    assert year_only.verdict == UNVERIFIED and year_only.weak  # a reissue has another year
    both = confirm.assess("/c/Even (Zidrou, Alexeï) (2021).cbz", one_shot)
    assert both.verdict == DOUBTFUL and len(both.weak) == 2


def test_scanner_and_publisher_words_are_not_authors():
    ev = confirm.assess("/c/Un livre (Digital) (Dupuis) (RIP-Club).cbz", album(volume=None, year=None))
    assert not ev.weak and not ev.strong


def test_reissue_year_does_not_outweigh_a_confirming_author():
    ev = confirm.assess("/c/Tiger Joe (Dupont) [2020].cbz", album(volume=None))
    assert ev.verdict == CONFIRMED and ev.weak


@pytest.mark.parametrize("verdict, looks", [(CONFIRMED, False), (UNVERIFIED, False), (DOUBTFUL, True), (CONTRADICTED, True)])
def test_only_doubtful_and_contradicted_need_a_look(verdict, looks):
    assert confirm.worth_a_look(confirm.Evidence(verdict=verdict)) is looks


# ------------------------------------------------------------------ in an import
def test_an_import_flags_a_match_the_album_page_contradicts(make_cbz):
    from helpers import ALBUM_URL, FakeWeb

    from .test_session import Bedetheque, Env, make_settings

    class Taker:
        def choose_match(self, path, matches):
            return matches[0].candidate

        def duplicate_action(self, path, others, what):
            return "skip"

    env = Env(FakeWeb(), make_settings(**{"import": {"auto": False, "series": "never"}}), Taker())
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    doubtful = make_cbz("Nains Redwin 1 (Smith, Jones) [1950].cbz", pages=3)
    env.session.run([doubtful])
    row = env.lib.get_file(doubtful)
    assert row["album_id"] is not None and "looks doubtful" in (row["review"] or "")  # linked, and flagged for a look


# ------------------------------------------------------------------ folders
def in_folder(*series, folder="/c/Comics"):
    return [(f"{folder}/f{n}.cbz", {"series": name}) for n, name in enumerate(series)]


def test_a_file_of_another_series_than_its_folders_is_an_outlier():
    items = in_folder("Blanche Neige (Trif)", "Blanche Neige (Trif)", "Blanche Neige (Trif)", "La poussière des Anges")
    found = confirm.folder_outliers(items)
    assert list(found) == ["/c/Comics/f3.cbz"] and "Blanche Neige (Trif)" in found["/c/Comics/f3.cbz"]


def test_a_folder_of_many_series_or_few_files_says_nothing():
    assert confirm.folder_outliers(in_folder("Batman Le Soir", "Batman Anthologie", "Spirou", "Lucky Luke", "Tintin")) == {}
    assert confirm.folder_outliers(in_folder("Nains", "Nains", "Elfes")) == {}  # three files: too few to say
    assert confirm.folder_outliers(in_folder("Batman", "Batman", "Batman", "Batman Année Un")) == {}  # share a word


# ------------------------------------------------------------------ ranking by what the album pages say
def test_a_contradicting_year_takes_nothing_away_and_an_unread_album_is_left_alone():
    from bdnex.importer.matching import Match, adjust_by_facts
    from bdnex.importer.source import Candidate

    read = Candidate("1", "u1", "plastique", 2)
    unread = Candidate("2", "u2", "plastique", 1)
    matches = [Match(read, 90.0, 90.0), Match(unread, 90.0, 90.0)]
    facts = {"1": {"year": 2005, "credits": {}, "publisher": None, "series": None}}
    ranked = adjust_by_facts(matches, "/c/Le Plastique (2017).cbz", {}, lambda c: facts.get(c.source_id))
    assert [m.score for m in ranked] == [90.0, 90.0]


def test_a_year_or_an_author_that_agrees_earns_a_few_points():
    from bdnex.importer.matching import Match, adjust_by_facts
    from bdnex.importer.source import Candidate

    cand = Candidate("1", "u1", "x", 1)
    facts = {"1": {"year": 2017, "credits": {"Writer": ["Even, Katia"]}, "publisher": "Dargaud", "series": None}}
    (match,) = adjust_by_facts([Match(cand, 80.0, 80.0)], "/c/Plastique (Katia Even) (2017).cbz", {}, lambda c: facts["1"])
    assert match.score == 85  # +2 for the year, +3 for the author


def test_a_source_without_facts_is_ranked_as_before():
    from bdnex.importer.matching import find_matches

    class Plain:
        def candidates(self, text, limit=5):
            from bdnex.importer.source import Candidate
            return [(Candidate("1", "u", "nains", 1), 90.0)]

    assert find_matches(Plain(), "/c/nains 1.cbz")[0].score >= 85


# ------------------------------------------------------------------ doubts: asked about even when the score is strong
class Index:
    """A source with a fixed candidate list, as `find_matches` needs one."""

    def __init__(self, hits, **kw):
        from bdnex.library.models import AlbumInfo, SeriesInfo

        self.hits, self._info = hits, lambda c: AlbumInfo("x", c.source_id, c.name, volume=c.volume,
                                                          series=SeriesInfo("x", "s", "Nains") if c.volume else None)
        self.__dict__.update(kw)

    def candidates(self, text, limit=5):
        return self.hits

    def partial_info(self, candidate):
        return self._info(candidate)


def cands(*scores):
    from bdnex.importer.source import Candidate

    return [(Candidate(str(n), f"u{n}", "nains redwin", 1), s) for n, s in enumerate(scores, start=1)]


def test_a_close_second_makes_a_strong_first_choice_doubtful():
    from bdnex.importer.matching import find_matches

    close = find_matches(Index(cands(97.0, 94.0)), "/c/Nains Redwin.cbz")
    assert close[0].score >= 90 and "3 point(s) behind" in close[0].doubt
    clear = find_matches(Index(cands(97.0, 80.0)), "/c/Nains Redwin.cbz")
    assert clear[0].doubt == ""


def test_the_margin_can_be_turned_off_and_a_named_album_is_never_doubted():
    from bdnex.importer.matching import find_matches

    assert find_matches(Index(cands(97.0, 96.0), min_margin=0), "/c/Nains Redwin.cbz")[0].doubt == ""


def test_the_files_own_tags_naming_another_series_make_it_doubtful(monkeypatch):
    from bdnex.importer import matching
    from bdnex.importer.matching import find_matches

    monkeypatch.setattr(matching, "_embedded_of", lambda source, path: {"Series": "Tout Autre Chose"})
    first = find_matches(Index(cands(99.0, 60.0)), "/c/Nains Redwin.cbz")[0]
    assert "own tags say" in first.doubt


def test_a_doubtful_strong_match_is_not_applied_unasked(make_cbz):
    """With --auto (strong only) a first choice that has a doubt is asked about, not applied."""
    from dataclasses import replace

    from .test_session import Env, make_settings
    from helpers import FakeWeb

    env = Env(FakeWeb(), make_settings(**{"import": {"auto": True, "series": "never"}}), None)
    from bdnex.importer.matching import Match
    from bdnex.importer.source import Candidate

    best = Match(Candidate("1", "u", "n", 1), 97.0, 97.0, doubt="another album is only 2 point(s) behind")
    assert env.session._select("/c/x.cbz", [best]) == (None, None)  # no prompter: not applied
    assert env.session._select("/c/x.cbz", [replace(best, doubt="")])[0] is not None
