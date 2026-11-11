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


def test_diff_reports_added_and_changed_values_only():
    old = {"Title": "A", "Series": "S", "Writer": "W", "Manga": "No", "Notes": "keep"}
    new = {"Title": "B", "Series": "S", "Year": "2015"}
    diff = comicinfo.diff_fields(old, new)
    assert diff == ["~ Title: A  ->  B", "+ Year: 2015"]
    assert comicinfo.diff_fields(new, new) == []


def test_a_field_the_database_lacks_is_never_a_difference():
    old = {"Format": "One Shot", "Writer": "W", "Title": "T"}
    assert comicinfo.diff_fields(old, {"Title": "T"}) == []  # nothing to decide: nothing is erased


def test_merge_database_wins_where_it_has_a_value_and_the_rest_is_kept():
    merged = comicinfo.merge_with_existing({"Title": "old", "Manga": "No", "Format": "One Shot"}, {"Title": "new"})
    assert merged == {"Title": "new", "Manga": "No", "Format": "One Shot"}


# --------------------------------------------------------------- readable differences
def test_change_is_a_plain_string_with_its_parts_attached():
    [change] = comicinfo.diff_fields({"Series": "A"}, {"Series": "B"})
    assert isinstance(change, str) and change == "~ Series: A  ->  B"
    assert (change.kind, change.field, change.before, change.after, change.note) == ("~", "Series", "A", "B", "")


def test_squash_collapses_newlines_and_runs_of_spaces():
    assert comicinfo.squash("a\n\n  b \t c ") == "a b c" and comicinfo.squash(None) is None


def test_short_values_are_shown_in_full():
    assert comicinfo.excerpts("a" * 100, "b" * 100) == ("a" * 100, "b" * 100, "")


def test_long_values_are_cut_around_their_first_difference():
    common = "mot " * 100
    before, after, note = comicinfo.excerpts(common + "UN reste", common + "DEUX reste")
    assert "UN" in before and "DEUX" in after and before.startswith("…") and len(before) <= 102
    assert note == f"({len(common) + 8} -> {len(common) + 10} characters, first difference at {len(common)})"
    assert after.split("UN")[0] == "" or "UN" not in after  # each side shows its own text


def test_a_long_value_present_on_one_side_only_is_cut_to_its_start():
    before, after, note = comicinfo.excerpts(None, "x" * 500)
    assert before is None and after == "x" * 100 + "…" and note == "(500 characters)"
    before, after, note = comicinfo.excerpts("y" * 500, None)
    assert after is None and before == "y" * 100 + "…"


def test_multiline_values_become_one_line_in_the_plain_text():
    [change] = comicinfo.diff_fields({"Summary": "line one\nline two"}, {"Summary": "line one\nline 2"})
    assert "\n" not in change and change == "~ Summary: line one line two  ->  line one line 2"


def test_a_huge_summary_no_longer_floods_the_output():
    old = {"Summary": ("Un texte. " * 400) + "fin A"}
    new = {"Summary": ("Un texte. " * 200) + "fin B"}
    [change] = comicinfo.diff_fields(old, new)
    assert len(change) < 400 and "characters, first difference at" in change
