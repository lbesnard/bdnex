"""A series page of bdtheque.com: parsed, kept compressed in its own table, linked to a file by address."""

import io
import os
import zlib

import pytest

from bdnex.importer import bdtheque
from bdnex.library import Library

URL = "https://www.bdtheque.com/series/14922/le-pere-des-etoiles-pere-ou-impairs"
PAGE = open(os.path.join(os.path.dirname(__file__), "bdtheque_series_14922.html"), encoding="utf-8").read()


def test_addresses():
    assert bdtheque.is_address(URL) and bdtheque.is_address("http://bdtheque.com/series/14922?x=1#avis")
    assert not bdtheque.is_address("https://www.bedetheque.com/serie-18658-BD-Boys.html")
    assert bdtheque.canonical("http://bdtheque.com/series/14922/slug?x=1#a") == "https://www.bdtheque.com/series/14922/slug"
    assert bdtheque.canonical("https://www.bdtheque.com/series/14922") == "https://www.bdtheque.com/series/14922"


def test_parse_the_series_page():
    page = bdtheque.parse_series_page(PAGE)
    assert page["id"] == "14922" and page["url"] == URL and page["title"] == "Le Père des étoiles (Père ou Impairs)"
    assert page["credits"] == {"Writer": ["Piquet (Sébastien)"], "Penciller": ["Piquet (Sébastien)"], "Colorist": ["Piquet (Sébastien)"]}
    assert page["publisher"] == "Dargaud" and page["genres"] == ["Humour"]
    assert (page["year"], page["month"], page["day"]) == (2015, 5, 22)
    assert page["tomes"] == 1 and page["rating"] == 3.2 and page["cover_url"].endswith("T_59602.JPG")
    assert page["themes"] == ["Geeks", "Maternité, paternité"] and page["extra"]["public"] == "Tous publics" and page["extra"]["type"] == "BD"
    assert page["summary"].startswith("Cet album") and page["summary"].endswith("(texte de l'éditeur)")
    with pytest.raises(ValueError):
        bdtheque.parse_series_page("<html><body>nothing</body></html>")


def test_one_tome_is_a_one_shot_and_several_take_the_volume_of_the_file():
    page = bdtheque.parse_series_page(PAGE)
    one = bdtheque.album_info(page, volume=3)  # "1 tome paru": a volume in the file name changes nothing
    assert one.series is None and one.volume is None and one.source == "bdtheque" and one.source_id == "14922"
    many = bdtheque.album_info({**page, "tomes": 4}, volume=3)
    assert many.series.name == page["title"] and many.volume == 3 and many.source_id == "14922-3" and many.title.endswith("T03")
    unknown = bdtheque.album_info({k: v for k, v in page.items() if k != "tomes"}, volume=2)  # no count on the page: the file's volume decides
    assert unknown.volume == 2 and unknown.series is not None


def test_the_page_is_fetched_once_and_kept_compressed(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return PAGE.encode()

    with Library(str(tmp_path / "lib.db")) as library:
        site = bdtheque.Bdtheque(library, fetch)
        info = site.album_info("http://bdtheque.com/series/14922?utm=1")
        assert info.title == "Le Père des étoiles (Père ou Impairs)" and calls == [URL.rsplit("/", 1)[0]]
        site.album_info(URL.rsplit("/", 1)[0])
        assert len(calls) == 1  # stored
        raw = library.conn.execute("SELECT content FROM bdtheque_pages").fetchone()[0]
        assert raw[:1] == b"x" and zlib.decompress(raw).decode().startswith("<!DOCTYPE")  # compressed, in its own table
        assert library.conn.execute("SELECT COUNT(*) FROM source_cache").fetchone()[0] == 0
        with pytest.raises(ValueError):
            site.album_info("https://example.com/x")


def test_a_challenge_page_or_a_wrong_page_is_not_stored(tmp_path):
    with Library(str(tmp_path / "lib.db")) as library:
        with pytest.raises(ValueError):
            bdtheque.Bdtheque(library, lambda url: b"<html><h2>nothing here</h2></html>").page(URL)
        assert library.bdtheque_get(URL) is None


def test_a_file_is_linked_to_the_album(tmp_path):
    from helpers import make_cbz_in  # noqa: F401


def test_a_file_is_linked_to_the_album(tmp_path, make_cbz):
    path = make_cbz("Pere des etoiles.cbz")
    with Library(str(tmp_path / "lib.db")) as library:
        info = bdtheque.album_info(bdtheque.parse_series_page(PAGE))
        album_id = bdtheque.link_file(library, path, info)
        album = library.get_album(album_id)
        assert album["source"] == "bdtheque" and album["publisher"] == "Dargaud" and album["year"] == 2015
        assert library.get_file(path)["album_id"] == album_id
        assert bdtheque.link_file(library, path, info) == album_id  # again: the same album


def test_import_album_with_a_bdtheque_address(tmp_path, make_cbz, monkeypatch):
    import yaml

    from bdnex.ui import import_cmd

    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    calls = []
    import bdnex.importer.bedetheque.fetchers as fetchers

    monkeypatch.setattr(fetchers, "build_fetcher", lambda settings: (lambda url: (calls.append(url), PAGE.encode())[1]))
    path = make_cbz("Pere des etoiles.cbz")
    out = io.StringIO()
    assert import_cmd.run(["--album", URL, "-n", path], out=out) == 0 and "Would read" in out.getvalue() and not calls  # a preview fetches nothing
    assert import_cmd.run(["--album", URL, path], out=io.StringIO()) == 0 and len(calls) == 1
    with Library(str(tmp_path / "lib.sqlite")) as library:
        assert library.get_album(library.get_file(path)["album_id"])["source"] == "bdtheque"
