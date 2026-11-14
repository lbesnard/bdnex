"""The metadata editor of the web page: the same edits as `bdnex edit`, with the same checks."""

import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api.test_web_features import HEADERS, client, env  # noqa: E402,F401
from bdnex.api import jobs  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402


def edit(web, album, preview=True, headers=HEADERS, **fields):
    return web.post(f"/api/albums/{album}/edit", json={"preview": preview, "fields": fields}, headers=headers)


def stored(settings, album):
    with Library.for_settings(settings, readonly=True) as lib:
        return lib.get_album(album)


def test_a_preview_shows_the_changes_and_stores_nothing(env):
    settings, book, album = env
    web = client(settings)  # changes are not even allowed: a preview needs no permission
    shown = edit(web, album, title="Un autre titre", year=2001, genres=["Humour"], credits={"Writer": ["Uderzo", "Goscinny"]})
    assert shown.status_code == 200 and shown.json()["changed"] is True and shown.json()["stored"] is False
    text = "\n".join(shown.json()["changes"])
    assert "Un autre titre" in text and "Writer" in text and "2001" in text
    assert stored(settings, album)["title"] == "Redwin"


def test_a_real_edit_needs_permission_the_header_and_an_idle_server(env, monkeypatch):
    settings, book, album = env
    assert edit(client(settings), album, preview=False, title="X").status_code == 403  # changes are off
    web = client(settings, allow_changes=True)
    assert edit(web, album, preview=False, headers={}, title="X").status_code == 403  # no X-Requested-With
    assert edit(web, album, preview=False, headers={**HEADERS, "Origin": "http://evil.example"}, title="X").status_code == 403
    release = threading.Event()
    monkeypatch.setitem(jobs.SPECS, "slow", jobs.Spec("slow", False, lambda options, settings: (lambda out: release.wait(5) and 0, [])))
    running = web.post("/api/jobs", json={"command": "slow"}).json()
    assert edit(web, album, preview=False, title="X").status_code == 409  # one writer at a time
    release.set()
    assert stored(settings, album)["title"] == "Redwin"


def test_a_real_edit_is_stored_and_kept_like_a_hand_edit(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    done = edit(web, album, preview=False, title="Le titre juste", publisher="Soleil", volume=3, month=5,
                genres=["Humour", "Aventure"], credits={"Writer": ["Uderzo"], "Colorist": ["Leonardo"]})
    assert done.status_code == 200 and done.json()["stored"] is True
    now = stored(settings, album)
    assert now["title"] == "Le titre juste" and now["publisher"] == "Soleil" and now["volume"] == 3 and now["month"] == 5
    assert sorted(now["genres"]) == ["Aventure", "Humour"] and now["credits"]["Writer"] == ["Uderzo"]
    assert now["extra"]["edits"]["title"] == "Le titre juste"  # recorded as an edit: a re-import does not undo it
    assert done.json()["album"]["title"] == "Le titre juste"


def test_an_unchanged_form_changes_nothing_and_a_role_can_be_removed(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    assert edit(web, album, preview=False, title="Redwin").json()["changed"] is False
    edit(web, album, preview=False, credits={"Writer": ["Uderzo"]})
    removed = edit(web, album, preview=False, credits={"Writer": []})
    assert removed.json()["changed"] is True and "Writer" not in stored(settings, album)["credits"]


def test_bad_values_are_refused_with_the_reason_and_nothing_is_stored(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    for fields in ({"month": 13}, {"year": "soon"}, {"volume": -1}, {"title": ["a"]}, {"nonsense": 1}, {"credits": ["x"]}):
        refused = edit(web, album, preview=False, **fields)
        assert refused.status_code == 400, fields
    assert edit(web, 9999, title="x").status_code == 404
    assert stored(settings, album)["title"] == "Redwin"


def test_renaming_the_series_renames_it_for_all_its_albums(env):
    settings, book, album = env
    with Library.for_settings(settings) as lib:
        other = lib.add_album(AlbumInfo("bedetheque", "11", "Bran", series=SeriesInfo("bedetheque", "1", "Nains"), volume=2, language="fr"))
    web = client(settings, allow_changes=True)
    shown = edit(web, album, series="Les Nains")
    assert "renames the series for all its albums" in "\n".join(shown.json()["changes"])
    edit(web, album, preview=False, series="Les Nains")
    assert stored(settings, other)["series"] == "Les Nains"


def test_the_id_query_term_finds_one_album(env):
    settings, book, album = env
    with Library.for_settings(settings, readonly=True) as lib:
        assert [r["id"] for r in lib.query([f"id:{album}"])] == [album]
        assert lib.query(["id:99999"]) == []


def test_a_bulk_edit_previews_then_applies_to_every_album_of_a_query(env):
    settings, book, album = env
    with Library.for_settings(settings) as lib:
        other = lib.add_album(AlbumInfo("bedetheque", "11", "Bran", series=SeriesInfo("bedetheque", "1", "Nains"), volume=2, language="fr"))
    web = client(settings, allow_changes=True)
    body = {"q": "series:nains", "set": ["publisher=Soleil", "genres=Fantasy; Humour", "credits.Writer=Jarry"]}
    shown = web.post("/api/edit", json=body).json()
    assert shown["matched"] == 2 and shown["changed"] == 2 and shown["stored"] is False
    assert stored(settings, album)["publisher"] in (None, "")
    done = web.post("/api/edit", json={**body, "preview": False}, headers=HEADERS).json()
    assert done["stored"] is True
    assert stored(settings, album)["publisher"] == "Soleil" and stored(settings, other)["publisher"] == "Soleil"
    assert sorted(stored(settings, other)["genres"]) == ["Fantasy", "Humour"]


def test_a_bulk_edit_refuses_an_empty_query_and_bad_terms(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    assert web.post("/api/edit", json={"q": "  ", "set": ["publisher=X"]}).status_code == 400  # every album: never
    assert web.post("/api/edit", json={"q": "series:nains", "set": []}).status_code == 400
    assert web.post("/api/edit", json={"q": "series:nains", "set": ["colour=red"]}).status_code == 400
    assert web.post("/api/edit", json={"q": "volume:abc", "set": ["publisher=X"]}).status_code == 400
    assert web.post("/api/edit", json={"q": "series:nains", "set": ["month=13"]}).status_code == 400
    assert web.post("/api/edit", json={"q": "series:nains", "set": ["publisher=X"], "preview": False}).status_code == 403
