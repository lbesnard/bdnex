"""``bdnex ls --sitemap``: the whole album list searched by words, or for the albums a file could be."""

import io
import json

import pytest

from bdnex.importer import album_search
from bdnex.importer.source import Candidate
from bdnex.ui import ls_cmd, sitemap_search


def album(source_id, slug, volume):
    return Candidate(source_id, f"https://www.bedetheque.com/BD-{slug}-{source_id}.html", slug.replace("-", " ").lower(), volume)


CATALOGUE = [album("1", "Yoko-Tsuno-Le-Trio-de-l-etrange", 1), album("2", "Yoko-Tsuno-La-Frontiere-de-la-vie", 2),
             album("3", "Yoko-Tsuno-Integrale-INT", None), album("4", "Batman-Dark-Victory", 1),
             album("5", "Batman-Un-Long-Halloween", 1), album("6", "Nains-Redwin-de-la-Forge", 1)]


class Source:
    def catalogue(self):
        return CATALOGUE


@pytest.fixture
def albums():
    return album_search.AlbumList(Source())


def names(hits):
    return [c.url.split("-", 1)[1].rsplit("-", 1)[0] for c, _ in hits]


def test_every_word_has_to_be_in_the_name_and_a_typo_is_forgiven(albums):
    hits, total = albums.search("batman victory")
    assert [c.source_id for c, _ in hits] == ["4"] and total == 1
    assert albums.search("batmn dark victry")[0][0][0].source_id == "4"  # no name has all the words as written: fuzzy
    assert albums.search("")[0] == [] and albums.search("zzzzzzzz")[0] == []


def test_a_file_is_searched_by_its_folder_when_its_name_says_little(albums):
    hits = album_search.search_file(albums, "/comics/Series/Yoko Tsuno/Tome 2 - La frontiere.cbr", limit=5)
    assert hits[0].candidate.source_id == "2"  # the series from the folder, the volume from the name
    assert any("Yoko Tsuno" in query for query in hits[0].found_by)


def test_the_volume_the_name_asks_for_comes_first(albums):
    hits = album_search.search_file(albums, "/comics/Yoko Tsuno/Yoko Tsuno - T01.cbz", limit=5)
    assert hits[0].candidate.source_id == "1"
    assert hits[0].parts and "volume" in hits[0].parts[0]


def test_the_command_gives_text_and_file_results_as_json(monkeypatch, tmp_path):
    import yaml

    from bdnex.library import Library

    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(db):
        pass
    monkeypatch.setattr(sitemap_search, "build", lambda settings, library: album_search.AlbumList(Source()))
    out = io.StringIO()
    assert ls_cmd.run(["--sitemap", "batman", "--for", "/c/Nains/Nains - T01.cbz", "--limit", "3", "--json"], out=out) == 0
    report = json.loads(out.getvalue())
    assert report[0]["query"] == "batman" and report[0]["albums"][0]["name"].startswith("Batman")
    assert report[1]["file"].endswith("Nains - T01.cbz") and report[1]["albums"][0]["name"].startswith("Nains")
    assert report[1]["albums"][0]["found_by"] and len(report[1]["albums"]) <= 3
    plain = io.StringIO()
    assert ls_cmd.run(["--sitemap", "batman"], out=plain) == 0 and "Batman" in plain.getvalue() and "\x1b[" not in plain.getvalue()
    assert ls_cmd.run(["--sitemap"], out=io.StringIO()) == 2
    count = io.StringIO()
    assert ls_cmd.run(["--sitemap", "batman", "-c"], out=count) == 0 and count.getvalue().strip() == "2"


def test_albums_that_tie_on_the_words_are_told_apart_by_the_volume_not_cut_before(albums, monkeypatch):
    many = [album(str(100 + n), f"Yoko-Tsuno-Integrale-Tome-{n}", n) for n in range(1, 40)]  # one name, 39 volumes: all tie on the words
    albums.catalogue[:] = [*CATALOGUE, *many]
    albums.folded = album_search.folded_names(albums.catalogue)
    hits = album_search.search_file(albums, "/comics/Yoko Tsuno/Yoko Tsuno - Integrale T31.cbz", limit=3)
    assert hits[0].candidate.volume == 31  # it would be dropped by a cut at `limit` before the volume is looked at


def test_the_files_own_words_decide_between_albums_a_short_query_ties(albums):
    hits = album_search.search_file(albums, "/comics/Batman/Batman - Dark Victory T01.cbz", limit=3)
    assert hits[0].candidate.source_id == "4" and any("words are in the name" in part for part in hits[0].parts)


@pytest.mark.parametrize("typed", ["batmandarkvic2", "batm dark vic 2", "bat dk victory 2", "BATMAN-dark-victory-2"])
def test_a_search_is_as_forgiving_as_fzf_letters_in_order_spaces_ignored_a_trailing_number_is_the_volume(albums, typed):
    albums.catalogue[:] = [*CATALOGUE, album("7", "Batman-Dark-Victory-Tome-2-Dark-Victory-2", 2), album("8", "Bakelandt-Tome-6-Les-armes-du-Victory", 6)]
    albums.folded = album_search.folded_names(albums.catalogue)
    hits, _ = albums.search(typed, limit=5)
    assert hits and hits[0][0].source_id in {"4", "7"} and all(c.source_id != "8" for c, _ in hits[:2])
    if "2" in typed:
        assert [c.source_id for c, _ in hits][0] == "7"  # the volume asked for wins


def test_a_file_is_also_searched_without_the_words_an_edition_adds():
    queries = album_search.file_queries("/comics/Yoko Tsuno/Yoko Tsuno - L'Integrale/Yoko Tsuno - Integrale T02.cbr")
    assert any(q.lower().startswith("yoko tsuno") and "integrale" not in q.lower() for q in queries)  # the series alone, with the volume
    assert len(queries) == len({q.lower() for q in queries})


def test_a_word_of_the_file_is_a_whole_word_of_the_name_not_a_piece_of_another_word(monkeypatch):
    """`Fell 02` must not rank `Rocketfellers Tome 2` as well as an album whose name has the word `fell`."""
    catalogue = [album("1", "Rocketfellers-Tome-2", 2), album("2", "Fell-Tome-2", 2)]
    monkeypatch.setattr(Source, "catalogue", lambda self: catalogue)
    hits = album_search.search_file(album_search.AlbumList(Source()), "/x/Comics/fell/Fell 02 (2005).cbz", limit=5)
    assert hits[0].candidate.source_id == "2"


def test_typing_letter_by_letter_gives_the_same_answers_as_asking_at_once(albums):
    """The albums of `batman dark` are looked for among those of `batman`: quicker, and never different."""
    cold = album_search.AlbumList(Source())
    for typed in ["b", "ba", "bat", "batman", "batman d", "batman dark", "batman dark vic", "dark batman", "batmandarkvic2", "yoko ts", "yoko"]:
        assert albums.search(typed) == album_search.search_folded(cold.catalogue, cold.folded, typed, 20)
    assert albums.search("batman dark")[1] == 1 and "pools" in albums.cache and "results" in albums.cache


def test_the_index_and_the_letter_pools_answer_exactly_as_the_plain_scan_does(monkeypatch):
    import random

    rnd = random.Random(5)
    syllables = "ba ta man dark vic to ry yo ko tsu no ast er ix lé gion ai re lu cky luke kil ling jo ke".split()
    names = [" ".join(rnd.choice(syllables) + rnd.choice(syllables) for _ in range(rnd.randint(1, 4))) for _ in range(3000)]
    catalogue = [album(str(i), n.replace(" ", "-"), rnd.choice([None, 1, 2, 3])) for i, n in enumerate(names)]
    folded = album_search.folded_names(catalogue)
    typed = ["ba", "bata", "batam", "batamandark", "bat dk vic2", "yoko tsu", "asterx legionaire", "kil jo", "zzzq", "bata man", "luckyluke3"]

    monkeypatch.setattr(album_search, "BIG_LIST", 10 ** 9)  # the plain scan
    plain = [album_search.search_folded(catalogue, folded, text, 20, {}) for text in typed]
    monkeypatch.setattr(album_search, "BIG_LIST", 0)  # the index on, and typed one letter after another with one cache
    cache: dict = {}
    indexed = [album_search.search_folded(catalogue, folded, text, 20, cache) for text in typed]
    assert indexed == plain and any(count for _, count in plain)
    assert "index" in cache and "letters" in cache
