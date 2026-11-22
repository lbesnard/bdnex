"""Findings of the review of the parsers: a label written a little differently is still that role, a block laid out differently does not lose the
album, a page cut short is not kept."""

import pytest

from bdnex.importer.bedetheque import Bedetheque, letters, parser
from bdnex.importer.bedetheque.bedetheque import IncompletePage
from bdnex.library import Library


@pytest.mark.parametrize("written", ["Scénario", "scenario", "SCÉNARIO :", "(Scénario)", "  Scénario  "])
def test_a_credit_label_is_the_role_whatever_its_case_accents_colon_or_parentheses(written):
    assert parser.canonical_label(written) == "Scénario"


def test_labels_bdnex_does_not_know_are_returned_as_written_and_the_other_roles_are_found_too():
    assert parser.canonical_label("Mystery role") == "Mystery role"
    assert parser.canonical_label("traduction :") == "Traduction" and parser.canonical_label("ENCRAGE") == "Encrage"
    assert parser.OTHER_CREDIT_ROLES[parser.canonical_label("encrage")] == "Inker"


def test_the_former_layout_with_an_infoedition_block_that_has_nothing_in_it_still_gives_the_album():
    html = ("<html><body><label>Série: </label><a href='/serie-1-BD-X.html'>X</a>"
            "<label>Titre: </label><span>Un titre</span>"
            "<span class='infoedition'></span><img alt='a' src='/a.jpg'><img alt='b'></body></html>")  # an empty block, an image with no address
    fields, _ = parser._parse_album_page_mobile(html, "https://m.bedetheque.com/BD-X-1.html")
    assert fields["album_url"] == "https://m.bedetheque.com/BD-X-1.html" and "cover_url" not in fields  # it raised, and the album was lost


def test_a_letter_page_cut_short_is_not_stored_and_the_series_of_that_letter_stay(monkeypatch):
    library = Library(":memory:")
    source = Bedetheque(library, fetch=lambda url: b"", sleep=lambda s: None)
    library.series_index_replace("bedetheque", [("1", "Nains", "nains", "https://www.bedetheque.com/serie-1-BD-Nains.html", "", "a")])
    pages = []

    def get(kind, url, recache=False):
        library.cache_put("bedetheque", kind, url, b"<html>cut")  # what _get does: it stores the page it downloaded
        pages.append(url)
        return b"<html>cut"

    monkeypatch.setattr(source, "_get", get)
    monkeypatch.setattr(letters, "parse_letter_page", lambda content, letter="": [])
    monkeypatch.setattr(letters, "announced_count", lambda content: 120)
    with pytest.raises(IncompletePage, match="cut short"):
        source.update_series_index()
    assert library.series_index_count("bedetheque") == 1  # the letter was not replaced by an empty list
    assert not library.cache_has("bedetheque", "letter", pages[0])  # and the cut page is not kept: it would be read again as it is
