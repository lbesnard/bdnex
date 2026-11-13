"""Word-based matching: query tokenising, folder hints, the volume cap and candidate scoring."""

import pytest

from bdnex.importer.bedetheque import Bedetheque, sitemap
from bdnex.importer.matching import (NO_VOLUME_CAP, find_matches, VOLUME_MISMATCH_CAP, queries_for, query_from_filename, rerank,
                                     series_queries)
from bdnex.importer.source import Candidate
from bdnex.library import Library


@pytest.mark.parametrize("text, words, volumes", [
    ("Bleach T68", ["bleach"], [68]),
    ("Bleach 68", ["bleach"], [68]),
    ("Astérix Tome 12", ["asterix"], [12]),
    ("Fog 02 Bonin Fr", ["fog", "bonin"], [2]),                    # "Fr" is noise
    ("L'Enfant de l'Orage v01 ALB 003", ["enfant", "orage"], [1]),  # v01 is a volume, ALB is noise
    ("Léonard 18 Génie en sous sol", ["leonard", "genie", "sous", "sol"], [18]),
    ("Mutafukaz Mutafukaz Meta", ["mutafukaz", "meta"], []),         # repeated words once
    ("La Vie Prive\\201e des Hommes", ["vie", "privee", "hommes"], []),  # escape left by a bad decode
])
def test_query_tokens(text, words, volumes):
    got_words, got_volumes = sitemap.query_tokens(text)
    assert got_words == words
    assert got_volumes == volumes


def test_explicit_volume_marker_beats_bare_numbers():
    assert sitemap.query_tokens("Titre 2021 T03")[1] == [3]


def test_folder_name_is_a_second_query_when_it_adds_words():
    assert queries_for("/c/Achille Talon/Tome 20 - Viva Papa.pdf") == [
        "20 Viva Papa", "Achille Talon 20 Viva Papa"]
    assert series_queries("/c/Achille Talon/Tome 20 - Viva Papa.pdf") == ["Achille Talon 20"]


def test_folder_name_is_skipped_when_the_file_already_has_it():
    assert queries_for("/c/Nains/Nains - T01 - Redwin.cbz") == ["Nains 1 Redwin"]
    assert series_queries("/c/Nains/Nains - T01 - Redwin.cbz") == ["Nains 1"]


def test_series_and_volume_queries_ignore_authors_and_years():
    assert "Loup Noir 5" in series_queries("/c/Loup Noir/Loup Noir - Intégrale 05 - 1973.cbr")
    assert series_queries("/c/Drain/Drain 02_Takeda_Fr.pdf") == ["Drain 2"]


def test_no_series_query_without_a_volume():
    assert series_queries("/c/Nains/Redwin.cbz") == []


def test_wrong_volume_is_never_a_strong_match():
    right = Candidate("1", "u1", "guerres civiles 3", volume=3)
    wrong = Candidate("2", "u2", "guerres civiles 1", volume=1)
    ranked = rerank("Guerres civiles T03", [(wrong, 100.0), (right, 96.0)])
    assert ranked[0].candidate is right
    assert ranked[1].score <= VOLUME_MISMATCH_CAP


@pytest.fixture
def source(tmp_path):
    src = Bedetheque(Library(tmp_path / "lib.sqlite"))
    names = [("1", "bleach 68 the ordinary peace", 68), ("2", "peach 6", 6),
             ("3", "rahan integrale soleil 1998 16 perle d eau", 16),
             ("4", "rahan collection altaya 40 premiere", 40), ("5", "druuna hs2 x", None)]
    src._index = [Candidate(i, f"https://x/{i}", n, v) for i, n, v in names]
    return src


def test_candidates_find_the_album_containing_all_the_words(source):
    cand, score = source.candidates("Bleach 68")[0]
    assert cand.source_id == "1" and score >= 70  # medium: the title is missing


def test_extra_words_in_the_album_name_cost_little(source):
    cand, score = source.candidates("Rahan 16 La perle d'eau")[0]
    assert cand.source_id == "3" and score >= 80


def test_misspelt_word_still_finds_candidates(source):
    assert source.candidates("Druna HS2")[0][0].source_id == "5"


def test_unknown_words_fall_back_to_a_full_scan(source):
    assert source.candidates("zzzz qqqq")  # never raises, still returns the nearest names


def test_series_and_volume_find_the_right_tome_among_many(tmp_path):
    src = Bedetheque(Library(tmp_path / "lib.sqlite"))
    src._index = [Candidate(str(i), f"https://x/{i}", f"loup noir taupinambour {i}", i) for i in range(1, 14)]
    ranked = rerank("Loup Noir Intégrale 5 1973", src.candidates("Loup Noir 5", limit=5))
    assert ranked[0].candidate.volume == 5 and ranked[0].score >= 70


def test_n_of_m_is_one_volume():
    assert query_from_filename("Le Clan des Chimeres 1 of 6_Suro_Fr.pdf") == "Le Clan des Chimeres 1 Suro Fr"


def test_album_without_volume_is_not_strong_for_a_file_with_one():
    named_like_the_series = Candidate("1", "u1", "ombres", volume=None)
    ranked = rerank("Ombres 02 Rollin", [(named_like_the_series, 100.0)])
    assert ranked[0].score <= NO_VOLUME_CAP


def test_series_only_matches_never_reach_strong():
    wrong = Candidate("1", "u1", "fete ombres 2", 2)

    class Stub:
        def candidates(self, query, limit=5):
            # the file name finds nothing close; only the "series volume" query finds this album
            return [(wrong, 95.0)] if query == "Ombres 2" else [(Candidate("9", "u9", "autre", 1), 20.0)]

    matches = find_matches(Stub(), "/c/Ombres/Ombres 02_Rollin_Fr.pdf")
    assert matches[0].candidate is wrong and matches[0].score < 90  # 95 + volume bonus, still capped


def test_a_series_score_is_capped_even_if_the_title_query_also_listed_the_album():
    wrong = Candidate("1", "u1", "fete ombres 2", 2)

    class Stub:
        def candidates(self, query, limit=5):
            return [(wrong, 95.0 if query == "Ombres 2" else 40.0)]

    assert find_matches(Stub(), "/c/Ombres/Ombres 02_Rollin_Fr.pdf")[0].score < 90


def test_a_file_that_names_its_album_matches_it_first(tmp_path):
    import zipfile
    from bdnex.importer.bedetheque import Bedetheque
    from bdnex.importer.matching import find_matches
    from bdnex.library import Library
    source = Bedetheque(Library(":memory:"), fetch=lambda url: b"", sleep=lambda s: None)
    source._index = []
    path = tmp_path / "completely unrelated name.cbz"
    url = "https://www.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ComicInfo.xml", f"<ComicInfo><Series>Nains</Series><Number>1</Number><Web>{url}</Web></ComicInfo>")
        archive.writestr("1.jpg", b"x")
    matches = find_matches(source, str(path))
    assert matches[0].candidate.url == url and matches[0].score == 100.0
    source.use_embedded = False
    assert find_matches(source, str(path)) == []  # without the tags nothing in the name leads anywhere
