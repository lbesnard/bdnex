"""A question can wait for hours: its picture, a link to each candidate's page, the 'waiting' signal and the kept history."""

import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api.test_web_features import HEADERS, client, env, wait  # noqa: E402,F401
from api.test_web_questions import answer, conflicted, waiting  # noqa: E402
from bdnex.api import jobs  # noqa: E402
from bdnex.api.webprompter import WebPrompter  # noqa: E402
from bdnex.importer.source import Candidate  # noqa: E402


def png(width=40, height=60):
    import pymupdf

    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.clear_with(180)
    return pixmap.tobytes("png")


def test_the_first_page_of_the_file_is_sent_with_the_match_question_and_cleared_after(make_cbz):
    seen = []
    prompter = WebPrompter(lambda question, image=None: seen.append((question, image)) or {"choice": 0})
    book = make_cbz("a.cbz", pages=2)
    prompter._thumbnail(book)
    prompter._select("Which album?", [{"name": "x", "value": 1}])
    assert seen[0][1] is not None and seen[0][1].startswith(b"\x89PNG")
    prompter._clear_thumbnail()
    prompter._select("Again", [{"name": "x", "value": 1}])
    assert seen[1][1] is None
    prompter._thumbnail("/no/such/file.cbz")  # a picture is a help: no picture, no error
    assert prompter._image is None


def test_each_candidate_carries_the_address_of_its_page_and_only_a_web_address():
    candidate = Candidate(source_id="1", url="https://www.bedetheque.com/BD-x-1.html", name="redwin")
    odd = Candidate(source_id="2", url="javascript:alert(1)", name="odd")
    seen = []
    prompter = WebPrompter(lambda question, image=None: seen.append(question) or {"choice": 0})
    prompter._select("Which?", [{"name": "[1] Redwin", "value": candidate}, {"name": "[2] Odd", "value": odd}, {"name": "Skip", "value": "skip"}])
    assert [c["url"] for c in seen[0]["choices"]] == ["https://www.bedetheque.com/BD-x-1.html", "", ""]


def test_the_picture_of_a_waiting_question_is_served_and_goes_with_the_answer(env, monkeypatch):
    settings, book, album = env

    def build(options, _settings):
        def run(out):
            from bdnex.ui import prompts

            prompter = prompts.new_prompter()
            prompter._draw_image(png())
            print("answer:", prompter._select("Pick one", [{"name": "A", "value": "a"}, {"name": "B", "value": "b"}]))
            return 0
        return run, []
    monkeypatch.setitem(jobs.SPECS, "pic", jobs.Spec("pic", True, build))
    web = client(settings, allow_changes=True)
    started = web.post("/api/jobs", json={"command": "pic", "preview": False, "interactive": True}, headers=HEADERS).json()
    job = waiting(web, started["id"])
    assert job["question"]["image"] is True
    served = web.get(f"/api/jobs/{started['id']}/image")
    assert served.status_code == 200 and served.content.startswith(b"\x89PNG") and served.headers["cache-control"] == "no-store"
    answer(web, job, choice=1)
    done = wait(web, started["id"])
    assert "answer: b" in "\n".join(done["lines"])
    assert web.get(f"/api/jobs/{started['id']}/image").status_code == 404  # nothing is waiting any more


def test_the_overview_says_which_task_is_waiting_for_an_answer(env):
    settings, book, album = env
    conflicted(book)
    web = client(settings, allow_changes=True)
    assert web.get("/api/overview").json()["waiting"] is None
    started = web.post("/api/jobs", json={"command": "write", "preview": False, "interactive": True}, headers=HEADERS).json()
    job = waiting(web, started["id"])
    waiting_for = web.get("/api/overview").json()["waiting"]
    assert waiting_for["job"] == started["id"] and waiting_for["message"] == "Which one wins?" and waiting_for["question"] == job["question"]["id"]
    web.post(f"/api/jobs/{started['id']}/cancel", headers=HEADERS)
    wait(web, started["id"])
    assert web.get("/api/overview").json()["waiting"] is None


def test_earlier_runs_survive_a_restart_and_a_run_cut_short_says_so(env, tmp_path):
    settings, book, album = env
    store = tmp_path / "history.json"
    first = jobs.JobManager(settings, store=store)
    job = first.start("check", {})
    for _ in range(100):
        if first.get(job.id).status != "running":
            break
        time.sleep(0.05)
    assert store.exists()
    records = json.loads(store.read_text())
    records.append({"id": 7, "command": "write", "label": "Write tags", "argv": [], "status": "running", "started": 1.0,
                    "finished": None, "exit_code": None, "lines": ["working"]})
    store.write_text(json.dumps(records))
    second = jobs.JobManager(settings, store=store)
    assert second.get(job.id).status in ("done", "failed") and second.get(7).status == "interrupted"
    assert "the server stopped while this was running" in second.get(7).lines[-1]
    assert second.start("check", {}).id == 8  # numbers go on


def test_a_broken_history_file_is_ignored(env, tmp_path):
    settings, book, album = env
    store = tmp_path / "history.json"
    store.write_text("{ not json")
    assert jobs.JobManager(settings, store=store).all() == []
    store.write_text(json.dumps([{"id": 1}]))
    assert jobs.JobManager(settings, store=store).all() == []
