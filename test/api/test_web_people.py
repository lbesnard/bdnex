"""People: each credited name is one row, and a name leads to the albums that have it."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api.test_web_features import client, env  # noqa: E402,F401
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402


@pytest.fixture
def crowd(env):
    settings, book, album = env
    with Library.for_settings(settings) as lib:
        series = SeriesInfo("bedetheque", "2", "Blueberry")
        lib.add_album(AlbumInfo("bedetheque", "21", "Fort Navajo", series=series, volume=1, language="fr",
                                credits={"Writer": ["Charlier, Jean-Michel"], "Penciller": ["Moebius"]}))
        lib.add_album(AlbumInfo("bedetheque", "22", "Tonnerre à l'ouest", series=series, volume=2, language="fr",
                                credits={"Writer": ["Moebius"], "Penciller": ["Moebius"], "Colorist": ["Moebius"]}))
        lib.add_album(AlbumInfo("bedetheque", "23", "Le Garage hermétique", language="fr", credits={"Writer": ["Moebius"]}))
    return settings


def test_a_name_is_one_row_with_its_albums_by_role(crowd):
    web = client(crowd)
    people = web.get("/api/people").json()
    names = [p["name"] for p in people["items"]]
    assert names[0] == "Moebius" and len(names) == len(set(names))  # one row per name, the busiest first
    moebius = people["items"][0]
    assert moebius["albums"] == 3 and moebius["roles"] == {"Penciller": 2, "Writer": 2, "Colorist": 1}
    assert people["count"] == len(names)


def test_people_can_be_searched_without_caring_for_case_or_accents_and_paged(crowd):
    web = client(crowd)
    assert [p["name"] for p in web.get("/api/people", params={"q": "MOEB"}).json()["items"]] == ["Moebius"]
    assert [p["name"] for p in web.get("/api/people", params={"q": "charlier"}).json()["items"]] == ["Charlier, Jean-Michel"]
    assert web.get("/api/people", params={"q": "zzz"}).json() == {"count": 0, "items": [], "offset": 0, "limit": 100}
    assert web.get("/api/people", params={"q": "%"}).json()["count"] == 0  # a percent sign is a percent sign
    second = web.get("/api/people", params={"limit": 1, "offset": 1}).json()
    assert second["count"] == 2 and [p["name"] for p in second["items"]] == ["Charlier, Jean-Michel"]


def test_the_exact_term_the_page_builds_finds_exactly_that_person(crowd):
    """`author:="Moebius"` and `writer:="..."`, quoted as a whole, are what a click on a name sends."""
    web = client(crowd)
    albums = lambda q: sorted(a["title"] for a in web.get("/api/albums", params={"q": q}).json()["items"])  # noqa: E731
    assert albums('"author:=Moebius"') == ["Fort Navajo", "Le Garage hermétique", "Tonnerre à l'ouest"]
    assert albums('"writer:=Moebius"') == ["Le Garage hermétique", "Tonnerre à l'ouest"]
    assert albums('"penciller:=Moebius"') == ["Fort Navajo", "Tonnerre à l'ouest"]
    assert albums('"author:=Charlier, Jean-Michel"') == ["Fort Navajo"]  # the comma stays inside the name
    assert albums('"author:=Charlier"') == []  # exact: a part of a name is not the name
    assert albums('"series:=Blueberry"') == ["Fort Navajo", "Tonnerre à l'ouest"]
