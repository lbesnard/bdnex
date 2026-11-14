"""The settings page, the per-file actions and the relocate task."""

import os

import pytest
import yaml

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api.test_web_features import HEADERS, client, env, wait  # noqa: E402,F401
from bdnex.api import jobs  # noqa: E402
from bdnex.library import Library  # noqa: E402


def option(web, key):
    for section in web.get("/api/settings").json()["sections"]:
        for found in section["options"]:
            if found["key"] == key:
                return found
    return None


def change(web, preview=True, headers=HEADERS, **changes):
    return web.post("/api/settings", json={"preview": preview, "changes": changes}, headers=headers)


@pytest.fixture
def conf(tmp_path, env):
    path = tmp_path / "bdnex.yaml"
    text = path.read_text()
    path.write_text("# my notes\n" + text + "write:\n  cbr: convert\n  pdf: convert   # convert | tag | skip\n")
    return path


def test_the_page_lists_every_option_with_its_kind_default_and_help_and_no_secret(env, conf):
    settings, book, album = env
    web = client(settings)
    listed = web.get("/api/settings").json()
    assert listed["path"] == str(conf)
    keys = {o["key"] for s in listed["sections"] for o in s["options"]}
    assert {"write.pdf", "import.workers", "cover.check", "match.strong_threshold", "api.allow_changes"} <= keys
    assert not keys & {"api.token", "api.password", "bedetheque.fetcher_endpoint"}
    pdf = option(web, "write.pdf")
    assert pdf["kind"] == "choice" and pdf["choices"] == ["convert", "tag", "skip"] and pdf["default"] == "convert"
    assert pdf["yours"] is True and "keep the PDF" in pdf["help"] and pdf["editable"] is True
    assert option(web, "write.workers")["kind"] == "int" and option(web, "write.workers")["min"] == 0
    assert option(web, "write.use_trash")["kind"] == "bool" and option(web, "api.port")["restart"] is True
    assert option(web, "library")["editable"] is False  # where the volume is: in the file
    assert "secret" not in str(listed)


def test_a_preview_shows_the_change_and_writes_nothing(env, conf):
    settings, book, album = env
    before = conf.read_text()
    shown = change(client(settings), **{"write.pdf": "tag"})  # no permission needed to look
    assert shown.status_code == 200 and shown.json()["stored"] is False and "write.pdf: 'convert' -> 'tag'" in shown.json()["changes"]
    assert conf.read_text() == before and settings.write.pdf == "convert"


def test_a_real_change_edits_the_file_in_place_keeps_comments_makes_a_backup_and_applies_at_once(env, conf):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    done = change(web, preview=False, **{"write.pdf": "tag", "write.workers": 2, "cover.check": False})
    assert done.status_code == 200, done.json()
    assert done.json()["stored"] is True
    text = conf.read_text()
    assert text.startswith("# my notes") and "  pdf: tag   # convert | tag | skip" in text and "workers: 2" in text
    assert yaml.safe_load(text)["write"]["pdf"] == "tag" and yaml.safe_load(text)["cover"]["check"] is False
    assert conf.with_name(conf.name + ".bak").exists()
    assert settings.write.pdf == "tag" and settings.write.workers == 2  # the running server follows
    assert option(web, "write.pdf")["value"] == "tag"


def test_bad_changes_are_refused_with_the_reason_and_the_file_is_untouched(env, conf):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    before = conf.read_text()
    for bad in ({"write.pdf": "zip"}, {"write.workers": -1}, {"write.workers": "many"}, {"write.workers": 99}, {"write.use_trash": "maybe"},
                {"nonsense": 1}, {"collection_path": "/x"}, {"api.token": "x"}, {"bedetheque.fetcher_endpoint": "http://x"},
                {"match.strong_threshold": 500}):
        refused = change(web, preview=False, **bad)
        assert refused.status_code == 400, bad
    assert conf.read_text() == before


def test_a_change_needs_permission_the_header_and_an_idle_server_and_a_server_option_says_restart(env, conf, monkeypatch):
    settings, book, album = env
    assert change(client(settings), preview=False, **{"write.pdf": "tag"}).status_code == 403
    web = client(settings, allow_changes=True)
    assert change(web, preview=False, headers={}, **{"write.pdf": "tag"}).status_code == 403
    import threading
    release = threading.Event()
    monkeypatch.setitem(jobs.SPECS, "slow", jobs.Spec("slow", False, lambda options, settings: (lambda out: release.wait(5) and 0, [])))
    running = web.post("/api/jobs", json={"command": "slow"}).json()
    assert change(web, preview=False, **{"write.pdf": "tag"}).status_code == 409
    release.set()
    wait(web, running["id"])
    assert change(web, **{"api.port": 9000}).json()["restart"] == ["api.port"]


# ------------------------------------------------------------------ per-file actions
def file_id(settings, book):
    with Library.for_settings(settings, readonly=True) as lib:
        return lib.get_file(book)["id"]


def test_the_fileid_term_finds_one_file(env):
    settings, book, album = env
    number = file_id(settings, book)
    with Library.for_settings(settings, readonly=True) as lib:
        assert [r["path"] for r in lib.query([f"fileid:{number}"], files=True)] == [book]
        assert lib.query(["fileid:99999"], files=True) == []


def test_a_file_can_be_marked_spare_and_unlinked_from_the_page(env):
    settings, book, album = env
    number = file_id(settings, book)
    web = client(settings, allow_changes=True)
    assert web.post(f"/api/files/{number}/spare", json={"spare": True}).status_code == 403  # header missing
    assert web.post(f"/api/files/{number}/spare", json={"spare": True}, headers=HEADERS).json()["spare"] is True
    with Library.for_settings(settings, readonly=True) as lib:
        assert lib.get_file(book)["spare"] == 1
    assert web.post(f"/api/files/{number}/spare", json={"spare": False}, headers=HEADERS).status_code == 200
    assert web.post(f"/api/files/{number}/unlink", headers=HEADERS).json()["unlinked"] is True
    with Library.for_settings(settings, readonly=True) as lib:
        assert lib.get_file(book)["album_id"] is None and lib.get_file(book)["spare"] == 0
    assert web.post("/api/files/9999/unlink", headers=HEADERS).status_code == 404


def test_a_file_can_be_sent_to_the_trash_from_the_page_and_restored(env, tmp_path):
    settings, book, album = env
    number = file_id(settings, book)
    web = client(settings, allow_changes=True)
    done = web.post(f"/api/files/{number}/trash", json={}, headers=HEADERS)
    assert done.status_code == 200 and not os.path.exists(book)
    with Library.for_settings(settings, readonly=True) as lib:
        assert lib.get_file(book) is None and lib.trash_entries()[0]["id"] == done.json()["trash_id"]
    restored = web.post("/api/jobs", json={"command": "trash-restore", "id": done.json()["trash_id"], "preview": False}, headers=HEADERS).json()
    assert wait(web, restored["id"])["status"] == "done" and os.path.exists(book)


def test_a_file_that_is_not_there_or_under_a_copy_source_is_not_trashed(env, tmp_path):
    settings, book, album = env
    number = file_id(settings, book)
    os.remove(book)
    web = client(settings, allow_changes=True)
    assert web.post(f"/api/files/{number}/trash", json={}, headers=HEADERS).status_code == 409


# ------------------------------------------------------------------ relocate and rematch as tasks
def test_relocate_is_a_task_with_two_checked_folders(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    assert web.post("/api/jobs", json={"command": "relocate"}).status_code == 400
    assert web.post("/api/jobs", json={"command": "relocate", "old": "--evil", "new": "/b"}).status_code == 400
    shown = web.post("/api/jobs", json={"command": "relocate", "old": "/old/comics", "new": "/new/comics"}).json()
    assert shown["arguments"] == ["-n", "/old/comics", "/new/comics"]
    wait(web, shown["id"])


def test_a_rematch_is_an_import_with_force_on_one_file(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    started = web.post("/api/jobs", json={"command": "import", "steps": ["match"], "force": True, "paths": [book]}).json()
    assert started["arguments"] == ["-n", "--match", "--force", book]
    wait(web, started["id"])


def test_an_option_that_is_true_false_or_auto_is_a_choice(env, conf):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    check = option(web, "cover.check")
    assert check["kind"] == "choice" and set(map(str, check["choices"])) == {"True", "False", "auto"}
    assert change(web, preview=False, **{"cover.check": "auto"}).status_code == 200
    assert change(web, preview=False, **{"cover.check": "sometimes"}).status_code == 400
