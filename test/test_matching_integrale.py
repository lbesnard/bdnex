"""A file that says "intégrale" is matched to the collected edition, not to a single tome that has the same words."""

from bdnex.importer import matching
from bdnex.importer.source import Candidate

BASE = "https://www.bedetheque.com/"
TOME5 = Candidate("5", BASE + "BD-Universal-War-One-Tome-5-Le-dernier-5.html", "universal war one 5 dernier", 5)
TOME1 = Candidate("1", BASE + "BD-Universal-War-One-Tome-1-La-guerre-1.html", "universal war one 1 guerre", 1)
INTEGRALE = Candidate("9", BASE + "BD-Universal-War-One-INT-9.html", "universal war one int", None)


class Source:
    name = "stub"
    use_embedded = False

    def candidates(self, text, limit=5):
        return [(TOME5, 90.0), (TOME1, 90.0), (INTEGRALE, 90.0)][:limit]


def first(path):
    return matching.find_matches(Source(), path)[0]


def test_the_word_is_read_in_the_whole_name_even_in_parentheses_and_brackets():
    for name in ("Universal War One (Intégrale) [HD]", "Rubrique-A-Brac(integrale)", "Rose (Alibert) [Intégrale, 2021]", "Les Intégrales"):
        assert matching.asks_for_integral(name), name
    for name in ("Nains - T01", "Intégralement vôtre", "Disintegrale"):
        assert not matching.asks_for_integral(name), name


def test_an_integrale_file_prefers_the_collected_edition_and_says_why():
    best = first("/x/Universal War One (Intégrale) [HD].cbz")
    assert best.candidate is INTEGRALE and any("intégrale and so is the album" in part for part in best.parts)
    tomes = [m for m in matching.find_matches(Source(), "/x/Universal War One (Intégrale) [HD].cbz") if m.candidate is not INTEGRALE]
    assert all(m.score < best.score for m in tomes) and any("the album is tome" in part for m in tomes for part in m.parts)


def test_a_file_with_a_volume_or_without_the_word_is_not_touched():
    with_volume = matching.find_matches(Source(), "/x/Universal War One (Intégrale) - T05.cbz")
    assert with_volume[0].candidate is TOME5  # the volume decides, as before
    plain = matching.find_matches(Source(), "/x/Universal War One.cbz")
    assert not any("intégrale" in part for m in plain for part in m.parts)


def test_without_a_collected_edition_among_the_candidates_nothing_is_taken_away():
    only_tomes = [(TOME5, 90.0), (TOME1, 88.0)]
    plain = matching.rerank("universal war one", only_tomes, [], None, integral=False)
    asked = matching.rerank("universal war one", only_tomes, [], None, integral=True)
    assert [(m.candidate, m.score) for m in plain] == [(m.candidate, m.score) for m in asked]  # a penalty would only lift unrelated albums


INT1 = Candidate("11", BASE + "BD-Universal-War-One-INT1-TT-11.html", "universal war one int1", None)
INT2 = Candidate("12", BASE + "BD-Universal-War-One-INT2-TT-12.html", "universal war one int2", None)


class SeveralEditions(Source):
    def candidates(self, text, limit=5):
        return [(TOME5, 90.0), (INT1, 90.0), (INT2, 90.0), (INTEGRALE, 90.0)][:limit]


def test_intégrale_2_is_the_second_collected_edition_and_a_plain_intégrale_the_whole_one():
    numbered = matching.find_matches(SeveralEditions(), "/x/Universal War One - Intégrale 2.cbz")
    assert numbered[0].candidate is INT2 and any("intégrale 2 asked for and found" in part for part in numbered[0].parts)
    plain = matching.find_matches(SeveralEditions(), "/x/Universal War One (Intégrale).cbz")
    by_id = {m.candidate.source_id: m.score for m in plain}
    assert plain[0].candidate is INTEGRALE and by_id["9"] > by_id["11"] == by_id["12"]  # the whole one first; "one of several" gets less
    assert matching.integral_number(INT2) == 2 and matching.integral_number(INTEGRALE) is None
