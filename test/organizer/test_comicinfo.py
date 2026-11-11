import pytest
import xmlschema

from bdnex.organizer import comicinfo

SERIES_ALBUM = {
    "title": "Redwin de la Forge", "series": "Nains", "kind": "series", "volume": 1, "alternate_number": None,
    "year": 2015, "month": 6, "day": 3, "pages": 56, "rating": 4.25, "summary": "Résumé & <co>",
    "publisher": "Soleil Productions", "isbn": "978-2-302-04644-3", "language": "fr", "format": "Grand format",
    "url": "https://www.bedetheque.com/BD-Nains-Tome-1-245127.html",
    "credits": {"Writer": ["Jarry, Nicolas"], "Penciller": ["Goux, Pierre-Denis", "Autre"]},
    "genres": ["Fantasy", "Aventure"]}
ONESHOT = {"title": "La bibliomule de Cordoue", "series": None, "kind": "oneshot", "volume": None,
           "alternate_number": None, "credits": {}, "genres": []}


def test_series_album_fields():
    f = comicinfo.album_to_fields(SERIES_ALBUM, page_count=3)
    assert f["Series"] == "Nains" and f["Number"] == "1" and f["Volume"] == "1"
    assert f["Writer"] == "Jarry, Nicolas" and f["Penciller"] == "Goux, Pierre-Denis, Autre"
    assert (f["Year"], f["Month"], f["Day"]) == ("2015", "6", "3")
    assert f["PageCount"] == "3"  # the real count wins over the site's "planches"
    assert f["CommunityRating"] == "4.25" and f["Genre"] == "Fantasy, Aventure"
    assert f["Notes"] == "ISBN: 978-2-302-04644-3" and f["LanguageISO"] == "fr"


def test_oneshot_uses_title_as_series_and_has_no_number():
    f = comicinfo.album_to_fields(ONESHOT)
    assert f == {"Title": "La bibliomule de Cordoue", "Series": "La bibliomule de Cordoue"}


def test_alternate_number_and_rating_clamped():
    f = comicinfo.album_to_fields({**SERIES_ALBUM, "volume": None, "alternate_number": "Hors-série", "rating": 9})
    assert "Number" not in f and f["AlternateNumber"] == "Hors-série" and f["CommunityRating"] == "5.00"


def test_xml_is_valid_roundtrips_and_escapes():
    data = comicinfo.build_xml(comicinfo.album_to_fields(SERIES_ALBUM, 3))
    assert data.startswith(b"<?xml") and b"&amp;" in data
    parsed = comicinfo.parse_xml(data)
    assert parsed["Summary"] == "Résumé & <co>" and parsed["Series"] == "Nains"


def test_invalid_field_rejected_by_schema():
    with pytest.raises(xmlschema.XMLSchemaException):
        comicinfo.build_xml({"Title": "x", "CommunityRating": "12"})


def test_diff_reports_added_removed_changed_and_ignores_foreign_fields():
    old = {"Title": "A", "Series": "S", "Writer": "W", "Manga": "No", "Notes": "keep"}
    new = {"Title": "B", "Series": "S", "Year": "2015"}
    diff = comicinfo.diff_fields(old, new)
    assert "~ Title: A  ->  B" in diff and "+ Year: 2015" in diff and "- Writer: W" in diff
    assert not any("Manga" in line for line in diff)  # a field bdnex never writes is not a conflict
    assert comicinfo.diff_fields(new, new) == []


def test_merge_keeps_foreign_fields_database_wins_on_managed():
    merged = comicinfo.merge_with_existing({"Title": "old", "Manga": "No"}, {"Title": "new"})
    assert merged == {"Manga": "No", "Title": "new"}
