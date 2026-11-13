"""On a series album, the page's <h1> is the series: the album's own title comes from its address."""

from bdnex.importer.bedetheque import parser

PAGE = """<section class="bdt-ah"><div class="bdt-ah-main"><div class="bdt-ah-top"><div>
<h1><a href="https://www.bedetheque.com/serie-1-BD-Aama.html">Aâma</a></h1></div></div>
<a class="bdt-img bdt-img--couv" href="x.jpg" title="Couverture de {cover}"><img alt="Couverture de {cover}"></a>
</div></section>"""
ADDRESS = "https://www.bedetheque.com/BD-Aama-Tome-2-La-multitude-invisible-172621.html"


def test_the_album_title_is_not_the_series():
    fields, _ = parser.parse_album_page(PAGE.format(cover="La multitude invisible"), ADDRESS)
    assert fields["Titre"] == "La multitude invisible" and fields["Série"] == "Aâma"


def test_accents_come_from_the_page_and_the_address_is_the_fallback():
    fields, _ = parser.parse_album_page(PAGE.format(cover="La multitude invisible"), ADDRESS.replace("multitude", "multitude"))
    assert "invisible" in fields["Titre"]
    accented = PAGE.format(cover="Les épées").replace("Aâma", "Aâma")
    fields, _ = parser.parse_album_page(accented, "https://www.bedetheque.com/BD-Aama-Tome-3-Les-epees-1.html")
    assert fields["Titre"] == "Les épées"
    fields, _ = parser.parse_album_page(PAGE.format(cover="other"), "https://www.bedetheque.com/BD-Aama-Tome-3-Les-epees-1.html")
    assert fields["Titre"] == "Les epees"


def test_an_album_with_no_title_in_its_address_keeps_the_series_name():
    fields, _ = parser.parse_album_page(PAGE.format(cover="x"), "https://www.bedetheque.com/BD-Aama-Tome-1-172620.html")
    assert fields["Titre"] == "Aâma"
