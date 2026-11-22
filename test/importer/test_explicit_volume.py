"""A name that SAYS its volume (`Tome 01`, `T01`, `Vol. 3`) is read as that volume, whatever bare number comes before it."""

import pytest

from bdnex.importer.folders import title_and_volume
from bdnex.importer.matching import explicit_volume, volumes_for


@pytest.mark.parametrize("name, volume", [
    ("Apocalypse Mania - CYCLE 2 - Tome 01 - Les lois du hasard.pdf", 1),  # `CYCLE 2` is the title's, not the volume
    ("Les Tuniques Bleues - Saison 3 - T07 - Titre.cbz", 7),
    ("Tome 2 - Les 3 mousquetaires.cbz", 2),  # the 3 is in the title
    ("Série - Vol. 4 - Épisode 9.cbz", 4),
    ("Nains - T01 - Redwin de la forge.cbz", 1),
    ("14-18 - T02.cbz", 2),
    ("Astérix Tome 12.cbz", 12),
])
def test_the_number_the_name_says_is_the_volume(name, volume):
    path = "/comics/" + name
    assert explicit_volume(path) == volume and volumes_for(path) == [volume]
    assert title_and_volume(path)[1] == volume


def test_the_bare_number_stays_a_word_of_the_title_not_the_volume():
    words, volume = title_and_volume("/x/Apocalypse Mania - CYCLE 2 - Tome 01 - Les lois du hasard.pdf")
    assert volume == 1 and "cycle" in words and "apocalypse" in words and "hasard" in words


@pytest.mark.parametrize("name, volume", [("100 Bullets - 01.cbz", 1), ("Death_Note_-_003lq.cbz", 3), ("Chapter 5 - X.cbz", 5)])
def test_a_name_that_does_not_say_it_is_read_as_before(name, volume):
    assert explicit_volume("/x/" + name) is None  # no marker: the old reading of the bare numbers is unchanged
    assert title_and_volume("/x/" + name)[1] == volume


def test_a_name_without_a_number_has_no_volume():
    assert explicit_volume("/x/Batman - Killing Joke.cbz") is None and volumes_for("/x/Batman - Killing Joke.cbz") == []
