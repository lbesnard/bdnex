"""Files in one folder: series, versions of one book, or unclear; and the series they vote for."""

import pytest

from bdnex.importer import folders
from bdnex.importer.bedetheque import Bedetheque
from bdnex.importer.folders import SERIES, UNCLEAR, VERSIONS, classify, propose_series, title_and_volume
from bdnex.importer.matching import Match
from bdnex.importer.source import Candidate
from bdnex.library import Library


def names(*files, folder="/c/Series/X"):
    return [f"{folder}/{name}" for name in files]


# ------------------------------------------------------------------ title and volume
@pytest.mark.parametrize("name, words, volume", [
    ("Aâma - T01 - L'odeur de la poussière chaude.cbz", ["aama", "l'odeur", "de", "la", "poussiere", "chaude"], 1),
    ("100 Bullets - 01.cbz", ["bullets"], 1),  # the leading number belongs to the title
    ("100 Bullets - 02.-.Vol.2.-.(Azzarello-Risso).cbz", ["bullets"], 2),
    ("12 Pilotes.cbr", ["pilotes"], 12),  # a lone number is the volume
    ("1984 (Coste).cbr", [], None),  # four digits: a year
    ("7 secondes V3 #3 (of 4) (2004).pdf", ["secondes"], 3),
    ("Un livre.cbz", ["un", "livre"], None),
])
def test_title_and_volume(name, words, volume):
    assert title_and_volume("/c/" + name) == (words, volume)


# ------------------------------------------------------------------- the three kinds
def test_files_numbered_alike_are_obviously_a_series():
    assert classify(names("Aâma - T01 - L'odeur.cbz", "Aâma - T02 - La multitude.cbz", "Aâma - T03 - Le désert.cbz")) == SERIES
    assert classify(names(*[f"A Game of Thrones (Dynamite) T{n:02d}.cbr" for n in (1, 2, 3)])) == SERIES
    assert classify(names("100 Bullets - 01.cbz", "100 Bullets - 02.-.Vol.2.-.(Azzarello-Risso).cbz")) == SERIES


def test_the_same_volume_twice_is_two_versions_of_it():
    assert classify(names("Foo - T01 - Titre.cbz", "Foo - T01 - Titre (N&B).cbz")) == VERSIONS
    assert classify(names("Foo - T01.cbz", "Foo - T01.cbr", "Foo - T01.pdf")) == VERSIONS
    # but a volume repeated among different ones leaves the user to say what the folder is
    assert classify(names("Foo - T01.cbz", "Foo - T01.cbr", "Foo - T02.cbz")) == UNCLEAR


def test_the_same_book_in_several_files_is_versions_not_a_series():
    assert classify(names("#J'accuse ...!.cbr", "#J'accuse ...!.pdf")) == VERSIONS
    assert classify(names("Aldobrando (Gipi, Critone) (N,B).cbr", "Aldobrando (Gipi, Critone).cbr")) == VERSIONS
    assert classify(names("Alex Clément est mort (2000).cbz", "Alex Clement est mort (Lepage, Rieu) (2000).cbr")) == VERSIONS


@pytest.mark.parametrize("files", [
    ["Achille Talon fait son ménage.pdf", "Et Hop.pdf", "Les_Impetueuses_T01.cbz"],  # mixed: some without a volume
    ["Foo T01.cbz", "Bar T02.cbz"],  # numbered, but not the same title
    ["Un livre.cbz", "Autre chose.cbz"],  # nothing in common
])
def test_anything_else_is_unclear(files):
    assert classify(names(*files)) == UNCLEAR


def test_a_lone_file_is_not_a_group():
    assert classify(names("a.cbz")) is None and classify([]) is None


# --------------------------------------------------------------------------- voting
def candidate(series, volume, ident):
    slug = series.replace(" ", "-")
    return Candidate(str(ident), f"https://www.bedetheque.com/BD-{slug}-Tome-{volume}-Titre-{ident}.html",
                     f"{series.lower()} {volume} titre", volume)


STRAY = {n: candidate("Stray Bullets", n, 100 + n) for n in (1, 2, 3)}
HUNDRED = {n: candidate("100 Bullets", n, 200 + n) for n in (1, 2, 3)}


@pytest.fixture
def source(tmp_path):
    src = Bedetheque(Library(tmp_path / "l.sqlite"))
    src._index = [*STRAY.values(), *HUNDRED.values()]
    return src


def hits(*pairs):
    return [Match(c, score, score) for c, score in pairs]


FILES = names("100 Bullets - 01.cbz", "100 Bullets - 02.cbz", "100 Bullets - 03.cbz", folder="/c/Series/100 Bullets")


def test_the_files_vote_and_an_outlier_is_pulled_into_the_series(source):
    matches = {FILES[0]: hits((STRAY[1], 91.5), (HUNDRED[1], 88.0)),  # alone, it would pick Stray Bullets
               FILES[1]: hits((HUNDRED[2], 95.0), (STRAY[2], 70.0)),
               FILES[2]: hits((HUNDRED[3], 95.0))}
    proposal = propose_series(source, FILES, matches)
    assert proposal.name == "100 Bullets" and proposal.covered == 3 and proposal.solid
    assert [proposal.mapping[f] for f in FILES] == [HUNDRED[1], HUNDRED[2], HUNDRED[3]]


def test_an_album_missing_from_the_files_own_matches_is_asked_of_the_source(source):
    matches = {FILES[0]: hits((HUNDRED[1], 90.0)), FILES[1]: hits((HUNDRED[2], 90.0)), FILES[2]: hits((STRAY[3], 60.0))}
    proposal = propose_series(source, FILES, matches)
    assert proposal.mapping[FILES[2]] == HUNDRED[3]  # found by asking for "100 Bullets 3"


def test_no_agreement_no_proposal(source):
    matches = {FILES[0]: hits((HUNDRED[1], 90.0)), FILES[1]: hits((STRAY[2], 90.0)), FILES[2]: hits((candidate("Other", 3, 9), 80.0))}
    assert propose_series(source, FILES, matches) is None


def test_files_without_a_volume_are_left_out_and_two_numbered_files_are_needed(source):
    mixed = names("100 Bullets - 01.cbz", "100 Bullets special.cbz", folder="/c/Series/100 Bullets")
    assert propose_series(source, mixed, {mixed[0]: hits((HUNDRED[1], 90.0)), mixed[1]: hits((HUNDRED[2], 80.0))}) is None


def test_a_series_that_shares_no_word_with_the_names_is_not_suggested(source):
    files = names("14-18 Tome 1.cbz", "14-18 Tome 2.cbz", folder="/c/Series/14-18")
    x = {n: Candidate(str(300 + n), f"https://www.bedetheque.com/BD-X-Tome-{n}-Titre-{300 + n}.html", f"x {n}", n) for n in (1, 2)}
    source._index = list(x.values())
    assert propose_series(source, files, {files[0]: hits((x[1], 66.0)), files[1]: hits((x[2], 66.0))}) is None


def test_solid_means_most_files_found_their_album(source):
    matches = {FILES[0]: hits((HUNDRED[1], 90.0)), FILES[1]: hits((HUNDRED[2], 90.0)), FILES[2]: []}
    source._index = [HUNDRED[1], HUNDRED[2]]  # volume 3 does not exist
    proposal = propose_series(source, FILES, matches)
    assert proposal.covered == 2 and proposal.total == 3 and proposal.solid
    four = FILES + names("100 Bullets - 04.cbz", folder="/c/Series/100 Bullets")
    weak = propose_series(source, four, {**matches, four[3]: []})
    assert weak.covered == 2 and weak.total == 4 and not weak.solid


def test_folder_name():
    assert folders.folder_name(FILES) == "100 Bullets"


def test_a_series_whose_title_is_made_of_numbers_is_a_series():
    paths = [f"/lib/Series/14-18/14-18 - T0{i}.pdf" for i in range(1, 9)] + ["/lib/Series/14-18/14-18 - T09.cbz"]
    assert folders.title_and_volume(paths[0]) == (["14", "18"], 1)
    assert folders.title_and_volume(paths[8]) == (["14", "18"], 9)
    assert folders.classify(paths) == folders.SERIES


def test_the_files_ticked_at_first_are_the_ones_named_like_most_of_the_series():
    from types import SimpleNamespace
    names = ["Les_Impetueuses_Tribulations_d_Achille_Talon_T01.cbz", "Les_Impetueuses_Tribulations_d_Achille_Talon_T02.cbz",
             *[f"Tome {n:02d} - Achille Talon et un titre.pdf" for n in range(1, 6)]]
    paths = [f"/x/{name}" for name in names]
    proposal = SimpleNamespace(mapping={path: object() for path in paths})  # the volumes of the cbz are in it too
    from bdnex.importer.folders import likely_members, name_shape
    assert name_shape(paths[0]).startswith("les impetueuses") and name_shape(paths[2]) == "tome"
    assert likely_members(proposal, paths) == set(paths[2:])


# ---------------------------------------------------- a series whose title is made of numbers
def numbered_catalogue(tmp_path):
    from bdnex.importer.bedetheque import sitemap
    slugs = [f"BD-14-18-Tome-{n}-Un-titre-{n}-{500 + n}.html" for n in range(1, 10)]
    slugs += [f"BD-666-Tome-{n}-Autre-{n}-{700 + n}.html" for n in range(1, 4)]
    slugs += ["BD-14-14-545606.html", "BD-R-15-Tome-2-188321.html", "BD-12-ans-Tome-14-356646.html",
              "BD-Gen-Tome-14-Gen-13-14-15815.html", "BD-I-s-Tome-14-I-s-14-21663.html"]  # what the matcher used to pick
    src = Bedetheque(Library(tmp_path / "n.sqlite"))
    src._index = [Candidate(sitemap.source_id_from_url(s), "https://www.bedetheque.com/" + s,
                            sitemap.normalise_slug("https://www.bedetheque.com/" + s),
                            sitemap.volume_from_url("https://www.bedetheque.com/" + s)) for s in slugs]
    return src


def test_a_title_made_of_numbers_gives_its_numbers_to_the_title_and_one_to_the_volume():
    from bdnex.importer.matching import numeric_title, volumes_for
    assert numeric_title("14 18 2".split()) == (["14", "18"], 2)
    assert numeric_title("666 1".split()) == (["666"], 1)
    assert numeric_title("100 bullets 1".split()) == ([], None)  # a lone leading number: the title has words
    assert numeric_title("tome 2".split()) == ([], None)
    assert volumes_for("/c/14-18/14-18 - T02.pdf") == [2]  # not [14, 18, 2]
    assert volumes_for("/c/Nains/Nains - T03.cbz") == [3]
    assert title_and_volume("/c/14-18/14-18 - T05.pdf") == (["14", "18"], 5)
    assert title_and_volume("/c/666/666 - 01 - (Froideva_-Tacito).cbr") == (["666"], 1)


def test_a_numeric_title_is_found_by_its_numbers_with_the_right_volume_first(tmp_path):
    from bdnex.importer.matching import find_matches
    src = numbered_catalogue(tmp_path)
    matches = find_matches(src, "/lib/Series/14-18/14-18 - T02.pdf")
    best = matches[0]
    assert best.candidate.url.endswith("BD-14-18-Tome-2-Un-titre-2-502.html") and best.score >= 85  # the series, not "14-14"
    assert "BD-14-14-545606.html" not in [m.candidate.url.rsplit("/", 1)[-1] for m in matches[:1]]
    three = find_matches(src, "/lib/Series/666/666 - 03 - (Froideva_-Tacito).cbr")
    assert three[0].candidate.url.endswith("BD-666-Tome-3-Autre-3-703.html")


def test_a_folder_of_a_numeric_series_is_taken_for_a_series(tmp_path):
    from bdnex.importer.matching import find_matches
    src = numbered_catalogue(tmp_path)
    paths = [f"/lib/Series/14-18/14-18 - T0{i}.pdf" for i in range(2, 9)] + ["/lib/Series/14-18/14-18 - T09.cbz"]
    assert classify(paths) == SERIES
    proposal = propose_series(src, paths, {path: find_matches(src, path) for path in paths})
    assert proposal is not None and proposal.name == "14 18" and proposal.solid
    assert [proposal.mapping[p].volume for p in paths] == [2, 3, 4, 5, 6, 7, 8, 9]
