"""More tasks on the web page: convert, reparse, init, trash and reset, with their options checked."""

import os
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api.test_web_features import HEADERS, client, env, wait  # noqa: E402,F401
from api.test_web_questions import answer, waiting  # noqa: E402
from bdnex.library import Library  # noqa: E402


def post(web, **body):
    return web.post("/api/jobs", json=body, headers=HEADERS)


def test_the_page_is_told_what_each_task_needs(env):
    settings, book, album = env
    items = {j["command"]: j for j in client(settings).get("/api/jobs/available").json()["items"]}
    assert {"init", "import", "convert", "reparse", "trash-empty", "reset"} <= set(items)
    assert "trash-restore" not in items  # started from the trash view, not a card
    assert items["convert"]["fields"] == ["query", "formats"] and items["init"]["preview"] is False
    assert items["reset"]["asks"] is True and items["write"]["fields"] == ["query"]


def test_init_has_no_preview(env):
    settings, book, album = env
    refused = client(settings).post("/api/jobs", json={"command": "init"})
    assert refused.status_code == 400 and "no preview" in refused.json()["detail"]


def test_convert_and_reparse_take_checked_options(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    assert post(web, command="convert", formats=["zip"]).status_code == 400
    preview = post(web, command="convert", formats=["pdf", "cbr"], query=["series:nains"]).json()
    assert preview["arguments"] == ["--from", "pdf", "--from", "cbr", "-n", "series:nains"]
    assert wait(web, preview["id"])["status"] in ("done", "failed")
    reparse = post(web, command="reparse", query=["series:nains"]).json()
    assert reparse["arguments"] == ["-n", "series:nains"]
    wait(web, reparse["id"])
    assert post(web, command="reparse", query=["--evil"]).status_code == 400


def test_trash_options_are_checked_and_an_unasked_empty_goes_ahead_only_when_told(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    assert post(web, command="trash-empty", older_than="soon").status_code == 422  # not a number
    assert post(web, command="trash-empty", older_than=-1).status_code == 400
    assert post(web, command="trash-restore").status_code == 400
    assert post(web, command="trash-restore", id=0).status_code == 400
    preview = post(web, command="trash-empty", older_than=30).json()
    assert preview["arguments"] == ["--empty", "--older-than", "30", "-n"]
    wait(web, preview["id"])
    unasked = post(web, command="trash-empty", preview=False).json()
    assert unasked["arguments"] == ["--empty", "--yes"]
    wait(web, unasked["id"])
    asked = post(web, command="trash-empty", preview=False, interactive=True).json()
    assert asked["arguments"] == ["--empty"]  # the page asks "Delete them for good?"
    wait(web, asked["id"])


def test_a_trashed_file_is_restored_from_the_page(env, tmp_path):
    settings, book, album = env
    trashed = tmp_path / "comics" / ".bdnex-trash" / "old.cbz"
    trashed.parent.mkdir(parents=True)
    trashed.write_bytes(b"x" * 10)
    original = tmp_path / "comics" / "old.cbz"
    with Library.for_settings(settings) as lib:
        lib.add_trash(str(original), str(trashed), "replaced", album_id=album)
        entry = lib.trash_entries()[0]["id"]
    web = client(settings, allow_changes=True)
    job = wait(web, post(web, command="trash-restore", id=entry, preview=False).json()["id"])
    assert job["status"] == "done" and original.exists() and not trashed.exists()


def test_reset_only_runs_after_the_page_asks_and_is_answered_yes(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    unasked = post(web, command="reset", preview=False)
    assert unasked.status_code == 400 and "confirm" in unasked.json()["detail"]
    started = post(web, command="reset", preview=False, interactive=True).json()
    job = waiting(web, started["id"])
    assert job["question"]["message"] == "Delete all of this from the library?"
    yes = next(c for c in job["question"]["choices"] if c["key"] == "y")
    assert answer(web, job, choice=yes["index"]).status_code == 200
    done = wait(web, started["id"])
    assert done["status"] == "done" and "The library is empty" in "\n".join(done["lines"])
    with Library.for_settings(settings, readonly=True) as lib:
        assert lib.stats()["albums"] == 0
    assert any(name.startswith("lib.sqlite.before-reset") for name in os.listdir(settings.library_path.parent))


def test_reset_answered_no_changes_nothing(env):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    started = post(web, command="reset", preview=False, interactive=True).json()
    job = waiting(web, started["id"])
    no = next(c for c in job["question"]["choices"] if c["key"] == "n")
    answer(web, job, choice=no["index"])
    assert "Nothing changed" in "\n".join(wait(web, started["id"])["lines"])
    with Library.for_settings(settings, readonly=True) as lib:
        assert lib.stats()["albums"] == 1
