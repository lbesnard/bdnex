"""The API next to the command line: candidates, doubtful matches, marks, review answers, history, windows, events."""

import time

import pytest
import yaml

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from bdnex.api.app import create_app  # noqa: E402
from bdnex.config import load_config  # noqa: E402
from bdnex.importer.bedetheque import Bedetheque  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402
from helpers import FakeWeb  # noqa: E402

HEADERS = {"X-Requested-With": "bdnex"}
FILE = "Nains - T01 - Redwin de la forge (Smith) [1950].cbz"


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    comics = tmp_path / "comics"
    comics.mkdir()
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "collection_path": str(comics), "library": str(tmp_path / "lib.sqlite"),
                                    "cover": {"dir": str(tmp_path / "covers"), "check": False},
                                    "bedetheque": {"delay": [0, 0]}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    settings = load_config().settings
    path = make_cbz(FILE, pages=3, directory=str(comics))
    with Library.for_settings(settings) as lib:
        Bedetheque(lib, fetch=FakeWeb(), sleep=lambda s: None).update_sitemaps()
        series = SeriesInfo("bedetheque", "1", "Autre")
        album = lib.add_album(AlbumInfo("bedetheque", "10", "Un autre album", series=series, volume=3, year=2001))
        lib.add_file(path, album)
        lib.flag_review(path, "another volume")
    return settings, path, album


def client(settings, **kw):
    return TestClient(create_app(settings, **kw), base_url="http://localhost")


def wait(web, job_id):
    for _ in range(100):
        job = web.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"the job did not finish: {job}")


# ---------------------------------------------------------------- candidates and doubtful matches
def test_candidates_come_with_the_evidence_of_each_score(env):
    settings, path, _ = env
    body = client(settings).get("/api/candidates", params={"q": "filename:Redwin", "per_file": 2}).json()
    entry = next(e for e in body["items"] if e["path"] == path)
    assert entry["now"] and entry["candidates"] and "why_score" in entry["candidates"][0]
    assert body["cover_compared"] is False and body["limit"] == 10


def test_candidates_are_a_window_of_the_files(env):
    settings, _, _ = env
    web = client(settings)
    assert web.get("/api/candidates", params={"limit": 1}).json()["items"]
    assert web.get("/api/candidates", params={"limit": 1, "offset": 5}).json()["items"] == []
    assert web.get("/api/candidates", params={"q": "year:"}).status_code == 400
    assert web.get("/api/candidates", params={"cover": "true", "limit": 10, "per_file": 5}).status_code == 400


def test_check_can_weigh_the_matches_too(env):
    settings, _, _ = env
    web = client(settings)
    plain = {f["key"] for f in web.get("/api/check").json()["items"]}
    assert "matches" not in plain
    keys = {f["key"] for f in web.get("/api/check", params={"matches": "true"}).json()["items"]}
    assert keys >= plain


# ---------------------------------------------------------------- windows, stats, fields
def test_albums_and_files_say_how_many_there_are_and_give_a_window(env):
    settings, _, _ = env
    with Library.for_settings(settings) as lib:
        for n in range(20, 26):
            lib.add_album(AlbumInfo("bedetheque", str(n), f"Album {n}", year=2010))
    web = client(settings)
    window = web.get("/api/albums", params={"limit": 2, "offset": 1}).json()
    assert window["count"] == 7 and len(window["items"]) == 2 and window["offset"] == 1
    assert web.get("/api/files", params={"limit": 1}).json()["count"] == 1
    assert web.get("/api/albums", params={"q": "year:2010", "limit": 100}).json()["count"] == 6


def test_stats_are_the_ones_of_bdnex_stats_and_fields_are_listed(env):
    settings, _, _ = env
    web = client(settings)
    stats = web.get("/api/stats").json()
    assert stats["albums"]["total"] >= 1 and stats["files"]["flagged_for_review"] == 1
    names = {f["name"]: f for f in web.get("/api/fields").json()["items"]}
    assert names["attr"]["help"] and names["year"]["kind"] == "number"


# ---------------------------------------------------------------- marks
def test_marks_are_set_found_again_and_shown_on_the_album(env):
    settings, path, album = env
    web = client(settings, allow_changes=True)
    preview = web.post("/api/marks", json={"q": "filename:Redwin", "set": {"checked": "keep"}, "preview": True})
    assert preview.json() == {"matched": 1, "changed": 0, "kind": "file"}
    assert web.get("/api/files", params={"q": "attr:checked"}).json()["count"] == 0
    done = web.post("/api/marks", json={"q": "filename:Redwin", "set": {"checked": "keep"}}, headers=HEADERS)
    assert done.json()["changed"] == 1
    assert web.get("/api/files", params={"q": "attr:checked=keep"}).json()["count"] == 1
    shown = web.get(f"/api/albums/{album}").json()
    assert shown["files"][0]["marks"] == {"checked": "keep"}
    web.post("/api/marks", json={"q": "filename:Redwin", "remove": ["checked"]}, headers=HEADERS)
    assert web.get("/api/files", params={"q": "attr:checked"}).json()["count"] == 0


def test_marks_need_the_change_permission_and_a_good_name(env):
    settings, _, _ = env
    assert client(settings).post("/api/marks", json={"q": "filename:Redwin", "set": {"a": "b"}}, headers=HEADERS).status_code == 403
    web = client(settings, allow_changes=True)
    bad = web.post("/api/marks", json={"q": "filename:Redwin", "set": {"Bad Name": "x"}}, headers=HEADERS)
    assert bad.status_code == 400 and "mark name" in bad.json()["detail"]
    assert web.post("/api/marks", json={"q": "", "set": {"a": "b"}}, headers=HEADERS).status_code == 400
    assert web.post("/api/marks", json={"q": "filename:Redwin"}, headers=HEADERS).status_code == 400


# ---------------------------------------------------------------- review: rematch and a chosen album
def test_review_can_apply_a_chosen_album_or_rematch_as_tasks(env):
    settings, path, _ = env
    web = client(settings, allow_changes=True)
    file_id = web.get("/api/review").json()["items"][0]["file_id"]
    answer = web.post(f"/api/review/{file_id}", json={"action": "album", "album": "245127"}, headers=HEADERS)
    assert answer.status_code == 200
    job = answer.json()["job"]
    assert job["command"] == "apply-album" and job["arguments"][:2] == ["--album", "245127"] and path in job["arguments"]
    assert "--database-only" in job["arguments"]  # a review answer never moves a file
    wait(web, job["id"])
    with Library.for_settings(settings) as lib:
        lib.flag_review(path, "another volume")  # the first task may have settled it
    again = web.post(f"/api/review/{file_id}", json={"action": "rematch", "interactive": True}, headers=HEADERS).json()["job"]
    assert again["command"] == "rematch" and "--force" in again["arguments"] and "--auto=strong" not in again["arguments"]
    web.post(f"/api/jobs/{again['id']}/cancel", headers=HEADERS)  # it waits for you to choose: that is the point
    wait(web, again["id"])


def test_review_refuses_a_bad_album_and_an_unknown_answer(env):
    settings, _, _ = env
    web = client(settings, allow_changes=True)
    file_id = web.get("/api/review").json()["items"][0]["file_id"]
    assert web.post(f"/api/review/{file_id}", json={"action": "album", "album": "--force"}, headers=HEADERS).status_code == 400
    assert web.post(f"/api/review/{file_id}", json={"action": "album"}, headers=HEADERS).status_code == 400
    assert web.post(f"/api/review/{file_id}", json={"action": "delete"}, headers=HEADERS).status_code == 400


# ---------------------------------------------------------------- komga and update as tasks
def test_komga_and_update_are_tasks_with_checked_options(env):
    settings, _, _ = env
    web = client(settings, allow_changes=True)
    listed = {j["command"] for j in web.get("/api/jobs/available").json()["items"]}
    assert {"update", "komga-show", "komga-push"} <= listed
    shown = web.post("/api/jobs", json={"command": "komga-show", "query": ["series:nains"], "only": "books"}).json()
    assert shown["arguments"] == ["show", "series:nains", "--only", "books"]
    bad = web.post("/api/jobs", json={"command": "komga-show", "only": "everything"})
    assert bad.status_code == 400
    push = web.post("/api/jobs", json={"command": "komga-push", "fields": ["title", "summary"], "with_review": True})
    assert push.json()["arguments"] == ["push", "-n", "--with-review", "--fields", "title,summary"]
    wait(web, shown["id"])


# ---------------------------------------------------------------- history
def test_changes_are_recorded_and_previews_and_refusals_are_not(env):
    settings, _, _ = env
    web = client(settings, allow_changes=True)
    web.post("/api/marks", json={"q": "filename:Redwin", "set": {"a": "b"}, "preview": True})
    web.post("/api/marks", json={"q": "filename:Redwin", "set": {"Bad Name": "b"}}, headers=HEADERS)  # refused
    assert web.get("/api/history").json()["count"] == 0
    web.post("/api/marks", json={"q": "filename:Redwin", "set": {"a": "b"}}, headers=HEADERS)
    history = web.get("/api/history").json()
    assert history["count"] == 1
    entry = history["items"][0]
    assert entry["path"] == "/api/marks" and entry["who"].startswith("local@") and entry["detail"]["set"] == {"a": "b"}


def test_the_history_names_the_login_and_keeps_the_newest_first(env):
    settings, _, _ = env
    web = client(settings, allow_changes=True, password="pw", username="laurent")
    login = ("laurent", "pw")
    for name in ("one", "two"):
        web.post("/api/marks", json={"q": "filename:Redwin", "set": {name: "1"}}, headers=HEADERS, auth=login)
    items = web.get("/api/history", auth=login).json()["items"]
    assert [i["detail"]["set"] for i in items] == [{"two": "1"}, {"one": "1"}] and items[0]["who"].startswith("laurent@")


def test_the_history_file_stays_small(tmp_path):
    from bdnex.api.history import History

    log = History(tmp_path / "h.jsonl", keep=20)
    for n in range(200):
        log.record("me", "POST", "/api/x", 200, {"n": n, "pad": "x" * 400})
    total, items = log.recent(5)
    assert total <= 25 and items[0]["detail"]["n"] == 199


# ---------------------------------------------------------------- events
def test_a_task_streams_its_output_as_events(env):
    settings, _, _ = env
    web = client(settings)
    job = web.post("/api/jobs", json={"command": "check"}).json()
    stream = web.get(f"/api/jobs/{job['id']}/events")
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: end" in stream.text and '"status": "done"' in stream.text or '"status": "failed"' in stream.text
    assert web.get("/api/jobs/999/events").status_code == 404


# ---------------------------------------------------------------- the review batch
def test_the_batch_lists_unmatched_files_and_a_decision_marks_them_checked(env):
    settings, path, _ = env
    other = path.replace("Redwin", "Zorn")
    import shutil
    shutil.copy(path, other)
    with Library.for_settings(settings) as lib:
        lib.add_file(other, None)
    web = client(settings, allow_changes=True)
    assert all(i["kind"] != "unmatched" for i in web.get("/api/review/batch").json()["items"])
    body = web.get("/api/review/batch", params={"unmatched": True}).json()
    row = next(i for i in body["items"] if i["path"] == other)
    assert row["kind"] == "unmatched" and body["total"] >= 2
    assert web.get("/api/review/batch", params={"unmatched": True, "q": "Zorn"}).json()["count"] == 1
    assert web.post(f"/api/review/{row['file_id']}/decide", json={"action": "leave"}).status_code == 403  # no header
    done = web.post(f"/api/review/{row['file_id']}/decide", json={"action": "leave"}, headers=HEADERS)
    assert done.json() == {"ok": True, "status": "left"}
    assert other not in [i["path"] for i in web.get("/api/review/batch", params={"unmatched": True}).json()["items"]]
    assert other in [i["path"] for i in web.get("/api/review/batch", params={"unmatched": True, "again": True}).json()["items"]]
    assert web.post(f"/api/review/{row['file_id']}/decide", json={"action": "delete"}, headers=HEADERS).status_code == 400
    assert web.post("/api/review/99999/decide", json={"action": "keep"}, headers=HEADERS).status_code == 404
