import pytest

from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.library.query import QueryError, fold


@pytest.fixture
def lib(make_cbz, tmp_path):
    library = Library(":memory:")
    nains = SeriesInfo("bedetheque", "47467", "Nains")
    asterix = SeriesInfo("bedetheque", "1", "Astérix")
    ids = {}
    ids["n1"] = library.add_album(AlbumInfo(
        "bedetheque", "245127", "Redwin de la Forge", series=nains, volume=1, publisher="Soleil Productions",
        year=2015, pages=56, rating=4.25, credits={"Writer": ["Jarry, Nicolas"], "Penciller": ["Goux, Pierre-Denis"]},
        genres=["Fantasy"]))
    ids["n2"] = library.add_album(AlbumInfo(
        "bedetheque", "245128", "Bran de Pierre", series=nains, volume=2, publisher="Soleil Productions",
        year=2016, pages=48, credits={"Writer": ["Jarry, Nicolas"]}, genres=["Fantasy"]))
    ids["a1"] = library.add_album(AlbumInfo(
        "bedetheque", "10", "Astérix le Gaulois", series=asterix, volume=1, publisher="Dargaud", year=1961,
        credits={"Writer": ["Goscinny, René"], "Penciller": ["Uderzo, Albert"]}, genres=["Humour", "Aventure"]))
    ids["bib"] = library.add_album(AlbumInfo(
        "bedetheque", "433432", "La bibliomule de Cordoue", publisher="Dargaud", year=2021, pages=251,
        credits={"Writer": ["Lupano, Wilfrid"], "Penciller": ["Chemineau, Léonard"]},
        genres=["Conte ou légende", "Satire"]))
    ids["partial"] = library.add_album(AlbumInfo(
        "bedetheque", "999", "Vide", extra={"partial": True}))
    ids["orphan"] = library.add_album(AlbumInfo("bedetheque", "5", "Sans fichier 100%", year=2000))

    files = {
        "n1": make_cbz("Nains - T01 - Redwin.cbz", pages=3),
        "n2": make_cbz("Nains - T02 - Bran.cbz", pages=4),
        "a1": make_cbz("Astérix 01.cbz", pages=5),
        "bib": make_cbz("La bibliomule de Cordoue.cbz", pages=6),
        "partial": make_cbz("vide.cbz", pages=7),
    }
    for key, path in files.items():
        library.add_file(path, ids[key])
    (tmp_path / "broken.cbz").write_bytes(b"not a zip")
    library.add_file(str(tmp_path / "broken.cbz"))  # untagged + corrupted
    library.paths = files
    yield library
    library.close()


def titles(rows):
    return [r["title"] for r in rows]


def test_no_terms_lists_everything_sorted_by_series_then_volume(lib):
    assert titles(lib.query([])) == ["Astérix le Gaulois", "La bibliomule de Cordoue", "Redwin de la Forge",
                                     "Bran de Pierre", "Sans fichier 100%", "Vide"]


def test_fold_removes_accents_and_case():
    assert fold("Astérix LE Gaulois") == "asterix le gaulois" and fold(None) == ""


def test_free_text_is_accent_insensitive_and_searches_title_series_authors_filename(lib):
    assert titles(lib.query(["asterix"])) == ["Astérix le Gaulois"]
    assert titles(lib.query(["uderzo"])) == ["Astérix le Gaulois"]  # author
    assert titles(lib.query(["bibliomule"])) == ["La bibliomule de Cordoue"]
    assert titles(lib.query(["T02"])) == ["Bran de Pierre"]  # file name


def test_terms_are_anded(lib):
    assert titles(lib.query(["series:nains", "year:2016"])) == ["Bran de Pierre"]
    assert lib.query(["series:nains", "publisher:dargaud"]) == []


def test_text_modes(lib):
    assert len(lib.query(["series:nain"])) == 2  # substring
    assert lib.query(["series:=nain"]) == [] and len(lib.query(["series:=NAINS"])) == 2  # exact
    assert titles(lib.query(["title::^la .*cordoue$"])) == ["La bibliomule de Cordoue"]  # regex, folded


def test_numeric_fields_and_ranges(lib):
    assert titles(lib.query(["year:2015..2016"])) == ["Redwin de la Forge", "Bran de Pierre"]
    assert titles(lib.query(["year:..1999"])) == ["Astérix le Gaulois"]
    assert titles(lib.query(["year:2020.."])) == ["La bibliomule de Cordoue"]
    assert titles(lib.query(["volume:2"])) == ["Bran de Pierre"]
    assert titles(lib.query(["rating:4.."])) == ["Redwin de la Forge"]


def test_credits_any_and_by_role(lib):
    assert len(lib.query(["author:jarry"])) == 2
    assert titles(lib.query(["penciller:uderzo"])) == ["Astérix le Gaulois"]
    assert lib.query(["writer:uderzo"]) == []  # Uderzo is only a penciller
    assert titles(lib.query(["writer:lupano"])) == ["La bibliomule de Cordoue"]


def test_genre_publisher_kind(lib):
    assert titles(lib.query(["genre:humour"])) == ["Astérix le Gaulois"]
    assert titles(lib.query(["genre:=satire"])) == ["La bibliomule de Cordoue"]
    assert len(lib.query(["publisher:dargaud"])) == 2
    assert {r["kind"] for r in lib.query(["kind:oneshot"])} == {"oneshot"}
    assert len(lib.query(["kind:series"])) == 3


def test_negation(lib):
    assert "Astérix le Gaulois" not in titles(lib.query(["^genre:humour"]))
    assert len(lib.query(["^genre:humour"])) == 5
    assert len(lib.query(["^series:nains"])) == 4  # albums without a series are kept


def test_file_fields_in_album_mode(lib):
    assert titles(lib.query(["filename:redwin"])) == ["Redwin de la Forge"]
    assert len(lib.query(["format:cbz"])) == 5  # every album with a file; not the file-less one
    assert len(lib.query(["size:1.."])) == 5 and lib.query(["size:..10"]) == []


def test_partial_and_file_count(lib):
    assert titles(lib.query(["partial:true"])) == ["Vide"]
    assert len(lib.query(["partial:false"])) == 5
    assert titles(lib.query(["files:0"])) == ["Sans fichier 100%"]
    assert lib.query(["files:0"])[0]["files"] == 0


def test_files_mode(lib):
    rows = lib.query([], files=True)
    assert len(rows) == 6 and all("path" in r and "file_id" in r for r in rows)
    assert [r["path"] for r in lib.query(["corrupted:true"], files=True)][0].endswith("broken.cbz")
    untagged = lib.query(["tagged:false"], files=True)
    assert len(untagged) == 1 and untagged[0]["id"] is None
    assert len(lib.query(["tagged:true", "author:jarry"], files=True)) == 2
    assert len(lib.query(["filename:asterix"], files=True)) == 1  # accents folded


def test_row_contents(lib):
    row = lib.query(["series:nains", "volume:1"])[0]
    assert row["authors"] == "Goux, Pierre-Denis; Jarry, Nicolas" and row["genres"] == "Fantasy"
    assert row["publisher"] == "Soleil Productions" and row["files"] == 1 and row["partial"] == 0


def test_literal_percent_is_not_a_wildcard(lib):
    assert titles(lib.query(["title:100%"])) == ["Sans fichier 100%"]
    assert titles(lib.query(["title:%"])) == ["Sans fichier 100%"]  # only the title containing a %
    assert lib.query(["title:_"]) == []  # nor is _ a single-character wildcard


def test_quotes_are_safe(lib):
    assert lib.query(["title:l'amour\"; DROP TABLE albums;--"]) == []
    assert lib.stats()["albums"] == 6


def test_unknown_field_prefix_is_free_text(lib):
    assert lib.query(["Tintin: Le Lotus"]) == []  # no error: "Tintin" is not a field


@pytest.mark.parametrize("terms", [["year:abc"], ["year:.."], ["corrupted:maybe"], ["series:"],
                                   ["title::(unclosed"], ["tagged:false"], ["rating:x..2"]])
def test_invalid_queries_raise(lib, terms):
    with pytest.raises(QueryError):
        lib.query(terms)


def test_files_count_not_allowed_in_files_mode(lib):
    with pytest.raises(QueryError):
        lib.query(["files:0"], files=True)


def test_any_and_slash_regex_search_everywhere(lib):
    from bdnex.library.query import compile_query, run_query
    for terms in (["any:bibliomule"], ["any::^la bibliomule"], ["/^la .*cordoue$/"], ["::bibliomule"],
                  ["title:/BIBLIOMULE/"]):
        assert any("bibliomule" in (r["title"] or "").lower() for r in lib.query(terms)), terms
    assert lib.query(["any:zzzzzzzz"]) == []
    assert lib.query(["^any:bibliomule"]) != lib.query([])
