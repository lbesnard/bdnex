"""Findings of the matching review: a wrong match must never look strong (and what is measured with tools/eval_matching.py stays so)."""

from types import SimpleNamespace

import pytest

from bdnex.config import ConfigError, build_settings
from bdnex.importer import confirm, folders, matching
from bdnex.importer.bedetheque.sitemap import query_tokens
from bdnex.importer.source import Candidate

BASE = "https://www.bedetheque.com/"


def cand(number, volume, name="nains"):
    return Candidate(str(number), f"{BASE}BD-Nains-Tome-{volume}-X-{number}.html", name, volume)


# ------------------------------------------------------------------ a bonus never undoes a cap
def test_a_year_and_an_author_do_not_lift_a_wrong_volume_above_its_cap():
    wrong = cand(1, 5)
    (match,) = matching.rerank("nains 2", [(wrong, 96.0)], [2], None)  # the file says tome 2, the album is tome 5
    assert match.score <= matching.VOLUME_MISMATCH_CAP and match.ceiling == matching.VOLUME_MISMATCH_CAP
    facts = lambda c: {"year": 2005, "credits": {"Writer": ["Jarry"]}}  # noqa: E731  (the page was read, and agrees on both)
    (lifted,) = matching.adjust_by_facts([match], "/x/Nains T02 (2005) (Jarry).cbz", {}, facts, None)
    assert lifted.score <= matching.VOLUME_MISMATCH_CAP  # it used to reach 90: strong, with the wrong volume
    open_ceiling = matching.Match(cand(2, 2), 80.0, 80.0)  # no cap: the bonus still counts
    (better,) = matching.adjust_by_facts([open_ceiling], "/x/Nains T02 (2005) (Jarry).cbz", {}, facts, None)
    assert better.score > 80.0


# ------------------------------------------------------------------ "Intégrale 2" is the second collected edition, not tome 2
TOME2 = Candidate("2", BASE + "BD-Asterix-Tome-2-La-serpe-2.html", "asterix 2 serpe", 2)
INT2 = Candidate("12", BASE + "BD-Asterix-INT2-TT-12.html", "asterix int2", None)


def test_an_integrale_with_a_number_prefers_that_collected_edition_to_the_same_numbered_tome():
    hits = [(TOME2, 90.0), (INT2, 90.0)]
    best = matching.rerank("asterix integrale 2", hits, [2], None, integral=True, integral_numbered=True)
    assert best[0].candidate is INT2 and any("the album is tome 2" in part for m in best for part in m.parts)
    marked = matching.rerank("asterix integrale 2", hits, [2], None, integral=True, integral_numbered=False)  # `Intégrale - T02`: tome 2
    assert marked[0].candidate is TOME2


# ------------------------------------------------------------------ what a name says
@pytest.mark.parametrize("name,expected", [
    ("Le Vol 714 pour Sydney - Herge.cbz", None),   # a title that holds a marker word and a number
    ("Tintin - T105.cbz", 105), ("Detective Conan T 12.cbz", 12), ("Asterix Tome 105.cbz", 105), ("Nains - Vol. 3.cbz", 3)])
def test_a_short_marker_with_three_digits_counts_only_when_glued_or_a_tome(name, expected):
    assert matching.explicit_volume("/x/" + name) == expected


def test_a_ligature_is_expanded_not_dropped():
    assert query_tokens("Le Cœur des Ténèbres T2") == (["coeur", "tenebres"], [2])
    assert query_tokens("Œil de Lynx 3")[0] == ["oeil", "lynx"]


# ------------------------------------------------------------------ a year off by one confirms nothing by itself
def test_a_year_off_by_one_is_not_a_confirmation_but_the_exact_year_is():
    album = {"title": "Redwin", "series": "Nains", "volume": 1, "year": 2007, "credits": {}}
    assert confirm.assess("/x/Nains - T01 (2006).cbz", album).verdict != confirm.CONFIRMED
    assert confirm.assess("/x/Nains - T01 (2007).cbz", album).verdict == confirm.CONFIRMED


# ------------------------------------------------------------------ folders
def test_a_shared_stopword_does_not_make_a_series_plausible():
    paths = ["/x/Les Tuniques Bleues/Les Tuniques Bleues - T01.cbz", "/x/Les Tuniques Bleues/Les Tuniques Bleues - T02.cbz"]
    assert not folders._plausible("Les Légendaires", paths)
    assert folders._plausible("Les Tuniques Bleues", paths)


def test_a_poor_candidate_is_no_vote_for_its_series():
    class Source:
        def partial_info(self, candidate):
            return SimpleNamespace(series=SimpleNamespace(source_id="s", name="Nains"))

        def candidates(self, text, limit=5):
            return []

    paths = ["/x/Nains/Nains - T01.cbz", "/x/Nains/Nains - T02.cbz"]
    weak = {p: [matching.Match(cand(n, n), folders.VOTE_MIN_SCORE - 10, 10.0)] for n, p in enumerate(paths, start=1)}
    assert folders.propose_series(Source(), paths, weak) is None
    strong = {p: [matching.Match(cand(n, n), 80.0, 80.0)] for n, p in enumerate(paths, start=1)}
    assert folders.propose_series(Source(), paths, strong) is not None


# ------------------------------------------------------------------ the configuration
def test_a_strong_threshold_below_the_medium_one_is_refused():
    with pytest.raises(ConfigError, match="strong_threshold"):
        build_settings({"config_version": 2, "match": {"strong_threshold": 50, "medium_threshold": 70}})
