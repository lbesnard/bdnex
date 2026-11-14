"""The read-only HTTP API and its web page."""

import os
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from bdnex.api.app import create_app  # noqa: E402
from bdnex.config import build_settings  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    user = {"config_version": 2, "collection_path": str(tmp_path / "comics"), "library": str(tmp_path / "lib.sqlite")}
    return build_settings(user)[0]


@pytest.fixture
def library(settings, tmp_path):
    os.makedirs(tmp_path / "comics")
    path = tmp_path / "comics" / "Nains - T01.cbz"
    path.write_bytes(b"x" * 100)
    with Library.for_settings(settings) as lib:
        series = SeriesInfo("bedetheque", "47467", "Nains")
        one = lib.add_album(AlbumInfo("bedetheque", "1", "Redwin de la Forge", series=series, volume=1, year=2015,
                                      language="fr", credits={"Writer": ["Jarry, Nicolas"]}, genres=["Fantasy"]))
        lib.add_album(AlbumInfo("bedetheque", "2", "La bibliomule", year=2020, language="fr"))
        lib.add_file(str(path), one)
        lib.add_trash(str(tmp_path / "comics" / "old.cbr"), str(tmp_path / "trash" / "old.cbr"), "converted", album_id=one)
    return settings


def client(settings, **kw):
    return TestClient(create_app(settings, **kw))


def test_home_is_the_web_page(library):
    page = client(library).get("/")
    assert page.status_code == 200 and "/static/app.js" in page.text and "text/html" in page.headers["content-type"]
    assert client(library).get("/static/app.js").status_code == 200 and client(library).get("/static/app.css").status_code == 200


def test_stats_and_health(library):
    c = client(library)
    assert c.get("/api/stats").json()["albums"] == 2
    health = c.get("/api/health").json()
    assert health["status"] == "ok" and health["token_required"] is False


def test_albums_with_the_ls_query_language(library):
    c = client(library)
    everything = c.get("/api/albums").json()
    assert everything["count"] == 2 and len(everything["items"]) == 2
    nains = c.get("/api/albums", params={"q": "series:nains"}).json()
    assert [a["title"] for a in nains["items"]] == ["Redwin de la Forge"]
    assert c.get("/api/albums", params={"q": "year:2019..2021"}).json()["count"] == 1
    assert c.get("/api/albums", params={"q": 'title:"la bibliomule"'}).json()["count"] == 1


def test_paging(library):
    page = client(library).get("/api/albums", params={"limit": 1, "offset": 1}).json()
    assert page["count"] == 2 and len(page["items"]) == 1 and page["offset"] == 1


def test_a_bad_query_is_a_400(library):
    answer = client(library).get("/api/albums", params={"q": "year:abc"})
    assert answer.status_code == 400 and "bad query" in answer.json()["detail"]
    assert client(library).get("/api/albums", params={"q": 'unbalanced "quote'}).status_code == 400


def test_one_album_with_its_files(library):
    c = client(library)
    first = c.get("/api/albums", params={"q": "series:nains"}).json()["items"][0]["id"]
    album = c.get(f"/api/albums/{first}").json()
    assert album["series"] == "Nains" and album["credits"]["Writer"] == ["Jarry, Nicolas"]
    assert len(album["files"]) == 1 and album["files"][0]["path"].endswith("Nains - T01.cbz")
    assert c.get("/api/albums/9999").status_code == 404


def test_files_series_and_trash(library):
    c = client(library)
    files = c.get("/api/files").json()
    assert files["count"] == 1 and files["items"][0]["path"].endswith("Nains - T01.cbz")
    series = c.get("/api/series").json()
    assert series["items"][0]["name"] == "Nains" and series["items"][0]["owned"] == 1
    assert c.get("/api/series", params={"missing": "true"}).json()["count"] == 0  # its album list is not known
    assert c.get("/api/series", params={"name": "zzz"}).json()["count"] == 0
    trash = c.get("/api/trash").json()
    assert trash["count"] == 1 and trash["items"][0]["reason"] == "converted"


def test_a_token_is_required_when_set(library):
    c = client(library, token="s3cret")
    assert c.get("/api/stats").status_code == 401
    assert c.get("/api/stats", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/stats", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/").status_code == 200  # the page itself asks for the token


def test_nothing_can_be_changed(library):
    c = client(library)
    assert c.post("/api/albums", json={}).status_code == 405
    assert c.delete("/api/albums/1").status_code == 405


def test_the_database_is_opened_read_only(library):
    with Library.for_settings(library, readonly=True) as lib:
        with pytest.raises(sqlite3.OperationalError):
            lib.conn.execute("DELETE FROM albums")


def test_an_old_schema_is_reported_not_migrated(library):
    conn = sqlite3.connect(library.library_path)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    answer = client(library).get("/api/stats")
    assert answer.status_code == 503 and "schema" in answer.json()["detail"]
    version = sqlite3.connect(library.library_path).execute("PRAGMA user_version").fetchone()[0]
    assert version == 1  # untouched


def test_no_library_yet(settings):
    answer = client(settings).get("/api/stats")
    assert answer.status_code == 503 and "no library" in answer.json()["detail"]


def test_an_album_cover_is_served_from_the_cover_folder(library):
    cover_dir = library.cover_path
    (cover_dir / "bedetheque").mkdir(parents=True)
    (cover_dir / "bedetheque" / "1.jpg").write_bytes(b"JPEGDATA")
    with Library.for_settings(library) as lib:
        album_id = lib.find_album("bedetheque", "1")
    with Library.for_settings(library) as lib:
        lib.set_cover_file(album_id, "bedetheque/1.jpg")
    web = client(library)
    got = web.get(f"/api/albums/{album_id}/cover")
    assert got.status_code == 200 and got.content == b"JPEGDATA"
    other = web.get("/api/albums/2/cover")
    assert other.status_code == 404  # nothing saved for it
    with Library.for_settings(library) as lib:
        lib.set_cover_file(album_id, "../../etc/passwd")
    assert web.get(f"/api/albums/{album_id}/cover").status_code == 404  # never outside cover.dir


# ------------------------------------------------------------------ a login for browsers (HTTP Basic)
def _basic(name, secret):
    import base64
    return {"Authorization": "Basic " + base64.b64encode(f"{name}:{secret}".encode()).decode()}


def test_a_password_asks_for_the_login_on_the_page_and_the_api(library):
    c = client(library, password="s3cret", username="me")
    for path in ("/", "/api/stats"):
        refused = c.get(path)
        assert refused.status_code == 401 and refused.headers["www-authenticate"].startswith("Basic")
    assert c.get("/", headers=_basic("me", "nope")).status_code == 401
    assert c.get("/", headers=_basic("you", "s3cret")).status_code == 401
    assert c.get("/", headers={"Authorization": "Basic !!!not base64"}).status_code == 401
    assert c.get("/api/stats", headers=_basic("me", "s3cret")).status_code == 200
    assert c.get("/", headers=_basic("me", "s3cret")).status_code == 200
    assert c.get("/api/health", headers=_basic("me", "s3cret")).json()["login_required"] is True


def test_a_password_with_a_colon_and_accents_works(library):
    c = client(library, password="mot:de passe é", username="lbesnard")
    assert c.get("/api/stats", headers=_basic("lbesnard", "mot:de passe é")).status_code == 200


def test_the_token_still_works_beside_a_password_and_the_health_check_needs_no_login(library):
    c = client(library, password="s3cret", token="tok")
    assert c.get("/api/stats", headers={"Authorization": "Bearer tok"}).status_code == 200
    health = c.get("/healthz")
    assert health.status_code == 200 and health.json() == {"status": "ok"}


def test_a_login_does_not_replace_the_other_protections_for_changes(library):
    c = client(library, password="s3cret", allow_changes=True)
    login = _basic("bdnex", "s3cret")
    assert c.post("/api/jobs", json={"command": "write", "preview": False}, headers=login).status_code == 403  # no X-Requested-With
    assert c.post("/api/jobs", json={"command": "write", "preview": False},
                  headers={**login, "X-Requested-With": "bdnex", "Origin": "http://evil.example"}).status_code == 403
