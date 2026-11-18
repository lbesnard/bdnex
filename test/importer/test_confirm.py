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
