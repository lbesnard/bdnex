"""The web page's API: more views, jobs, and the safeguards around changes."""

import os
import time

import pytest
import yaml

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from bdnex.api.app import create_app  # noqa: E402
from bdnex.config import build_settings, load_config  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402

HEADERS = {"X-Requested-With": "bdnex"}


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    comics = tmp_path / "comics"
    comics.mkdir()
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "collection_path": str(comics), "library": str(tmp_path / "lib.sqlite"),
                                    "cover": {"dir": str(tmp_path / "covers")}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    settings = load_config().settings
    book = make_cbz("one.cbz", pages=3, directory=str(comics))
    with Library.for_settings(settings) as lib:
        series = SeriesInfo("bedetheque", "1", "Nains")
        one = lib.add_album(AlbumInfo("bedetheque", "10", "Redwin", series=series, volume=1, language="fr", cover_url="u"))
        lib.add_file(book, one)
        lib.flag_review(book, "the cover differs from the album's (12% < 40%)")
    return settings, book, one


def client(settings, **kw):
    return TestClient(create_app(settings, **kw))


def wait(web, job_id, **headers):
    for _ in range(100):
        job = web.get(f"/api/jobs/{job_id}", headers=headers).json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


def test_overview_and_config_show_the_state_without_secrets(env):
    settings, book, album = env
    web = client(settings, token="s3cret")
    auth = {"Authorization": "Bearer s3cret"}
    overview = web.get("/api/overview", headers=auth).json()
    assert overview["files"] == 1 and overview["review"] == 1 and overview["allow_changes"] is False
    config = web.get("/api/config", headers=auth).json()
    assert config["api"]["token_required"] is True and "s3cret" not in str(config) and "fetcher_endpoint" not in str(config)
    assert web.get("/api/overview").status_code == 401


def test_the_review_queue_and_the_first_page_of_a_file(env):
    settings, book, album = env
    web = client(settings)
    item = web.get("/api/review").json()["items"][0]
    assert item["album"]["title"] == "Redwin" and "12%" in item["reason"] and item["album"]["has_cover"] is False
    with Library.for_settings(settings) as lib:  # a saved cover is reported (the file query rows do not carry it)
        (settings.cover_path / "bedetheque").mkdir(parents=True)
        (settings.cover_path / "bedetheque" / "10.png").write_bytes(b"x")
        lib.set_cover_file(item["album"]["id"], "bedetheque/10.png")
    assert web.get("/api/review").json()["items"][0]["album"]["has_cover"] is True
    picture = web.get(f"/api/files/{item['file_id']}/cover")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/png" and picture.content[:4] == b"\x89PNG"
    assert web.get("/api/files/999/cover").status_code == 404


def test_answering_a_review_needs_changes_to_be_allowed(env):
    settings, book, album = env
    file_id = client(settings).get("/api/review").json()["items"][0]["file_id"]
    assert client(settings).post(f"/api/review/{file_id}", json={"action": "keep"}, headers=HEADERS).status_code == 403
    web = client(settings, allow_changes=True)
    assert web.post(f"/api/review/{file_id}", json={"action": "keep"}).status_code == 403  # no custom header
    assert web.post(f"/api/review/{file_id}", json={"action": "keep"},
                    headers={**HEADERS, "Origin": "http://evil.example"}).status_code == 403  # another site
    assert web.post(f"/api/review/{file_id}", json={"action": "maybe"}, headers=HEADERS).status_code == 400
    assert web.post(f"/api/review/{file_id}", json={"action": "keep"}, headers=HEADERS).status_code == 200
    assert web.get("/api/review").json()["count"] == 0
    assert web.post(f"/api/review/{file_id}", json={"action": "keep"}, headers=HEADERS).status_code == 404


def test_unlinking_from_the_review_leaves_the_file_unmatched(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    file_id = web.get("/api/review").json()["items"][0]["file_id"]
    assert web.post(f"/api/review/{file_id}", json={"action": "unlink"}, headers=HEADERS).status_code == 200
    with Library.for_settings(settings) as lib:
        assert lib.get_file(book)["album_id"] is None and lib.get_file(book)["review"] is None


def test_check_and_duplicates_views(env):
    settings, book, album = env
    web = client(settings)
    keys = {f["key"] for f in web.get("/api/check").json()["items"]}
    assert "review" in keys
    assert web.get("/api/duplicates").json()["count"] == 0


def test_a_check_job_runs_in_the_background_and_shows_its_output(env):
    settings, book, album = env
    web = client(settings)  # a read-only job needs no permission
    started = web.post("/api/jobs", json={"command": "check"})
    assert started.status_code == 200
    job = wait(web, started.json()["id"])
    assert job["status"] == "failed" and any("wait for a review" in line for line in job["lines"])  # check exits 1 on findings
    assert web.get("/api/jobs").json()["items"][0]["id"] == job["id"]
    assert web.get(f"/api/jobs/{job['id']}?after={job['next']}").json()["lines"] == []  # only what is new


def test_a_preview_of_move_changes_nothing_and_a_real_one_is_refused_without_permission(env):
    settings, book, album = env
    before = open(book, "rb").read()
    web = client(settings)
    job = wait(web, web.post("/api/jobs", json={"command": "move"}).json()["id"])
    assert job["arguments"][0] == "-n" and open(book, "rb").read() == before
    real = web.post("/api/jobs", json={"command": "move", "preview": False}, headers=HEADERS)
    assert real.status_code == 403 and "api.allow_changes" in real.json()["detail"]


def test_a_real_job_runs_when_changes_are_allowed(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    assert web.post("/api/jobs", json={"command": "write", "preview": False}).status_code == 403  # the header is missing
    started = web.post("/api/jobs", json={"command": "write", "preview": False}, headers=HEADERS)
    assert started.status_code == 200 and started.json()["arguments"][0] == "--force"
    job = wait(web, started.json()["id"])
    assert job["status"] == "done" and "1 written" in "\n".join(job["lines"])
    import zipfile
    assert "ComicInfo.xml" in zipfile.ZipFile(book).namelist()


def test_only_the_listed_jobs_with_checked_options(env, tmp_path):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    assert web.post("/api/jobs", json={"command": "rm -rf /"}).status_code == 400
    outside = web.post("/api/jobs", json={"command": "record", "paths": ["/etc"]})
    assert outside.status_code == 400 and "outside your collection" in outside.json()["detail"]
    assert web.post("/api/jobs", json={"command": "write", "query": ["--force"]}).status_code == 400  # a term, never a flag
    assert web.post("/api/jobs", json={"command": "undo", "batch": "../x"}).status_code == 400
    names = {j["command"] for j in web.get("/api/jobs/available").json()["items"]}
    assert {"check", "move", "write", "undo"} <= names


def test_one_job_at_a_time(env, monkeypatch):
    settings, book, album = env
    import threading
    release = threading.Event()
    from bdnex.api import jobs
    monkeypatch.setitem(jobs.SPECS, "slow", jobs.Spec("slow", False, lambda options, settings: (lambda out: release.wait(5) and 0, [])))
    web = client(settings)
    first = web.post("/api/jobs", json={"command": "slow"})
    assert first.status_code == 200
    assert web.post("/api/jobs", json={"command": "check"}).status_code == 409
    assert web.get("/api/overview").json()["busy"] is True
    release.set()
    wait(web, first.json()["id"])


def test_a_running_job_can_be_cancelled(env, monkeypatch):
    settings, book, album = env
    from bdnex.api import jobs

    def forever(out):
        while True:
            time.sleep(0.01)

    monkeypatch.setitem(jobs.SPECS, "forever", jobs.Spec("forever", False, lambda options, settings: (forever, [])))
    web = client(settings, allow_changes=True)
    started = web.post("/api/jobs", json={"command": "forever"}).json()
    assert web.post(f"/api/jobs/{started['id']}/cancel", headers=HEADERS).status_code == 200
    assert wait(web, started["id"])["status"] == "cancelled"


def test_a_series_shows_each_volume_owned_or_missing(env):
    settings, book, album = env
    import json
    with Library.for_settings(settings) as lib:
        series_id = lib.conn.execute("SELECT id FROM series").fetchone()[0]
        lib.conn.execute("UPDATE series SET extra = ? WHERE id = ?", (json.dumps({"albums": [
            {"source_id": "10", "title": "Redwin", "volume": 1, "url": "u1"},
            {"source_id": "11", "title": "Bran", "volume": 2, "url": "u2"}]}), series_id))
        lib.conn.commit()
    detail = client(settings).get(f"/api/series/{series_id}").json()
    assert detail["list_known"] and [(v["volume"], v["owned"]) for v in detail["volumes"]] == [(1, True), (2, False)]
    assert client(settings).get("/api/series/999").status_code == 404
