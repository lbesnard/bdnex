import pytest

from bdnex.organizer import template

ALBUM = {"kind": "series", "series": "Nains", "title": "Redwin de la Forge", "volume": 1,
         "alternate_number": None, "language": "fr", "year": 2015, "publisher": "Soleil", "isbn": "x",
         "genres": ["Fantasy"], "credits": {"Writer": ["Jarry, Nicolas"], "Penciller": ["Goux"]}}
SERIES_TPL = "Series/$series/$series - T$volume:02 - $title"


def render(tpl, album=ALBUM, missing=lambda f: "Unknown"):
    return template.render(tpl, template.fields_for(album), missing)


def test_series_template_with_zero_padding():
    assert render(SERIES_TPL).parts == ["Series", "Nains", "Nains - T01 - Redwin de la Forge"]


def test_oneshot_template_with_authors():
    album = {**ALBUM, "kind": "oneshot", "series": None, "volume": None}
    parts = render("OneShot/$language/$title - $authors/$title - $authors", album).parts
    assert parts == ["OneShot", "fr", "Redwin de la Forge - Jarry, Nicolas, Goux",
                     "Redwin de la Forge - Jarry, Nicolas, Goux"]


def test_braces_and_unpadded_numbers():
    assert render("${series}/T$volume").parts == ["Nains", "T1"]


def test_value_with_slash_cannot_create_directories():
    parts = render("$series/$title", {**ALBUM, "title": "AC/DC: Live? <1>"}).parts
    assert parts == ["Nains", "AC_DC_ Live_ _1_"]


def test_illegal_characters_and_trailing_dots_removed():
    assert template.sanitize('a:b*c?"d".. ') == "a_b_c__d_"
    assert template.sanitize("") == "_" and len(template.sanitize("x" * 500)) == template.MAX_COMPONENT


def test_alternate_number_replaces_missing_volume():
    album = {**ALBUM, "volume": None, "alternate_number": "Hors-série"}
    assert render("$series - $volume", album).parts == ["Nains - Hors-série"]


def test_missing_field_uses_resolver_value_and_asks_once_per_field():
    asked = []
    album = {**ALBUM, "credits": {}}
    result = template.render("$authors/$title - $authors", template.fields_for(album),
                             lambda f: asked.append(f) or "Anon")
    assert result.parts == ["Anon", "Redwin de la Forge - Anon"] and asked == ["authors"]


def test_missing_field_skip():
    album = {**ALBUM, "credits": {}}
    result = render("$authors/$title", album, missing=lambda f: None)
    assert result.parts == [] and result.skipped_field == "authors"


def test_template_fields():
    assert template.template_fields("A/$series - T$volume:02/${title}") == ["series", "volume", "title"]
