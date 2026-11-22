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
    assert parts == ["Nains", "AC_DC - Live_ _1_"]  # (`: ` opens a subtitle: ` - `; the others are `_`)


def test_illegal_characters_and_trailing_dots_removed():
    assert template.sanitize('a:b*c?"d".. ') == "a_b_c__d_"
    assert template.sanitize("Batman: Dark Victory (1999)") == "Batman - Dark Victory (1999)"  # a subtitle, as a person writes it
    assert template.sanitize("Astérix : Le Gaulois") == "Astérix - Le Gaulois" and template.sanitize("12:30 train?") == "12_30 train_"
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


# ------------------------------------------------------------------ %functions{...}
VALUES = {"series": "Les Nains", "title": "Redwin é", "volume": 3, "authors": "A, B, C", "year": "", "kind": "series"}


def parts(template):
    from bdnex.organizer.template import render

    return render(template, VALUES, lambda name: "Unknown").parts


def test_text_functions():
    assert parts("%upper{$series}/%lower{$series}/%title{$title}") == ["LES NAINS", "les nains", "Redwin É"]
    assert parts("%capitalize{LES NAINS}") == ["Les nains"] and parts("%left{$series,3}/%right{$series,5}") == ["Les", "Nains"]
    assert parts("%asciify{$title}") == ["Redwin e"]


def test_if_and_ifdef_use_only_the_branch_they_take_and_never_ask_for_the_value_they_test():
    from bdnex.organizer.template import render

    asked = []
    result = render("%if{$year,($year) }$title/%ifdef{year,yes,no}/%if{$volume,T$volume:02}",
                    VALUES, lambda name: asked.append(name) or "Unknown")
    assert result.parts == ["Redwin é", "no", "T03"] and asked == []  # an empty year was tested, not asked for


def test_first_takes_some_items_of_a_list():
    assert parts("%first{$authors}") == ["A"] and parts("%first{$authors,2}") == ["A, B"]
    assert parts("%first{$authors,2,1}") == ["B, C"]


def test_functions_nest_and_a_typo_is_found_when_the_configuration_is_read():
    import pytest

    from bdnex.config import _check_template
    assert parts("%upper{%left{$series,3}}") == ["LES"]
    for bad in ("%uper{$series}", "%ifdef{nofield,x}", "%upper{$series"):
        with pytest.raises(ValueError):
            _check_template(bad)
    assert _check_template("%upper{$series}/%if{$volume,T$volume:02}$title") 


def test_a_year_the_site_added_to_a_series_name_can_be_left_out():
    assert template.strip_year("Batman: Dark Victory (1999)") == "Batman: Dark Victory"
    assert template.strip_year("Les Nains") == "Les Nains" and template.strip_year("Spirou (1938) Hors-série") == "Spirou (1938) Hors-série"  # only a trailing one
    assert template.strip_year("(1999)") == "(1999)"  # never nothing
