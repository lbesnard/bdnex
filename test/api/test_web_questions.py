"""Questions on the web page: a real run asks, the page answers, the same code goes on."""

import time
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api.test_web_features import HEADERS, client, env, wait  # noqa: E402,F401  (the same library and helpers)
from bdnex.api import jobs  # noqa: E402
from bdnex.api.webprompter import WebPrompter  # noqa: E402
from bdnex.organizer import comicinfo  # noqa: E402


def conflicted(book):
    with zipfile.ZipFile(book, "a") as zf:
        zf.writestr("ComicInfo.xml", b"<ComicInfo><Title>Old title</Title></ComicInfo>")


def waiting(web, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = web.get(f"/api/jobs/{job_id}").json()
        if job["question"] or job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("no question came")


def answer(web, job, **body):
    return web.post(f"/api/jobs/{job['id']}/answer", json={"question": job["question"]["id"], **body}, headers=HEADERS)


def test_a_conflict_is_asked_on_the_page_and_the_answer_is_used(env):
    settings, book, album = env
    conflicted(book)
    web = client(settings, allow_changes=True)
    started = web.post("/api/jobs", json={"command": "write", "preview": False, "interactive": True}, headers=HEADERS).json()
    assert started["arguments"] == [] and started["interactive"] is True  # not --force: it may ask
    job = waiting(web, started["id"])
    question = job["question"]
    assert question["kind"] == "select" and question["message"] == "Which one wins?"
    by_key = {c["key"]: c for c in question["choices"]}
    assert {"d", "w", "k", "s", "a"} <= set(by_key) and by_key["d"]["tone"] == "green" and by_key["a"]["tone"] == "red"
    assert answer(web, job, choice=by_key["k"]["index"]).status_code == 200  # keep the file as it is
    done = wait(web, started["id"])
    assert done["status"] == "done" and done["question"] is None
    assert zipfile.ZipFile(book).read("ComicInfo.xml") == b"<ComicInfo><Title>Old title</Title></ComicInfo>"


def test_the_other_answer_changes_the_file(env):
    settings, book, album = env
    conflicted(book)
    web = client(settings, allow_changes=True)
    started = web.post("/api/jobs", json={"command": "write", "preview": False, "interactive": True}, headers=HEADERS).json()
    job = waiting(web, started["id"])
    database = next(c for c in job["question"]["choices"] if c["key"] == "d")
    assert answer(web, job, choice=database["index"]).status_code == 200
    wait(web, started["id"])
    assert b"Redwin" in zipfile.ZipFile(book).read("ComicInfo.xml")


def test_a_bad_or_late_answer_is_refused_and_the_question_stays(env):
    settings, book, album = env
    conflicted(book)
    web = client(settings, allow_changes=True)
    started = web.post("/api/jobs", json={"command": "write", "preview": False, "interactive": True}, headers=HEADERS).json()
    job = waiting(web, started["id"])
    assert answer(web, job, choice=99).status_code == 400
    assert web.post(f"/api/jobs/{job['id']}/answer", json={"question": 999, "choice": 0}, headers=HEADERS).status_code == 409
    assert web.post(f"/api/jobs/{job['id']}/answer", json={"question": job["question"]["id"], "choice": 0}).status_code == 403  # no header
    assert web.get(f"/api/jobs/{job['id']}").json()["question"] is not None  # still waiting
    web.post(f"/api/jobs/{job['id']}/cancel", headers=HEADERS)
    assert wait(web, started["id"])["status"] == "cancelled"  # a job waiting for an answer can be cancelled


def test_without_asking_a_real_run_still_never_asks(env):
    settings, book, album = env
    conflicted(book)
    web = client(settings, allow_changes=True)
    started = web.post("/api/jobs", json={"command": "write", "preview": False}, headers=HEADERS).json()
    assert started["arguments"] == ["--force"] and started["interactive"] is False
    assert wait(web, started["id"])["status"] == "done" and b"Redwin" in zipfile.ZipFile(book).read("ComicInfo.xml")


def test_a_preview_is_never_interactive(env):
    settings, book, album = env
    web = client(settings)
    started = web.post("/api/jobs", json={"command": "write", "interactive": True}).json()
    assert started["interactive"] is False and started["arguments"][0] == "-n"
    wait(web, started["id"])


def test_the_import_job_checks_its_options_and_never_asks_unless_told(env, tmp_path):
    settings, book, album = env
    web = client(settings, allow_changes=True)
    assert web.post("/api/jobs", json={"command": "import", "steps": ["rm"]}).status_code == 400
    assert web.post("/api/jobs", json={"command": "import", "auto": "yes"}).status_code == 400
    assert web.post("/api/jobs", json={"command": "import", "files": "everything"}).status_code == 400
    assert web.post("/api/jobs", json={"command": "import", "paths": ["/etc"]}).status_code == 400
    preview = web.post("/api/jobs", json={"command": "import", "steps": ["match"], "files": "database_only"}).json()
    assert preview["arguments"] == ["-n", "--match", "--database-only"]
    wait(web, preview["id"])
    real = web.post("/api/jobs", json={"command": "import", "preview": False, "files": "database_only"}, headers=HEADERS).json()
    assert real["arguments"] == ["--auto=all", "--database-only"]  # nobody to ask: never ask
    wait(web, real["id"])
    asking = web.post("/api/jobs", json={"command": "import", "preview": False, "interactive": True}, headers=HEADERS).json()
    assert asking["arguments"] == [] and asking["interactive"] is True
    job = waiting(web, asking["id"])  # no album list here: the page is asked whether to download it
    assert "Download it now" in job["question"]["message"]
    web.post(f"/api/jobs/{asking['id']}/cancel", headers=HEADERS)
    wait(web, asking["id"])


# ------------------------------------------------------------------ the prompter itself
def asked(prompter_method, answers):
    """Run ``prompter_method(prompter)`` against a fake page that gives these answers; also return the questions."""
    seen = []

    def page(question, image=None):
        seen.append(question)
        return answers.pop(0)
    return prompter_method(WebPrompter(page)), seen


def test_every_kind_of_question_reaches_the_page_and_comes_back():
    value, seen = asked(lambda p: p._text("Words?", "abc"), [{"text": "x y"}])
    assert value == "x y" and seen[0] == {"kind": "text", "message": "Words?", "default": "abc"}
    choices = [{"name": "Ten", "value": 10}, {"name": "Twenty", "value": 20}]
    assert asked(lambda p: p._select("Which?", choices), [{"choice": 1}])[0] == 20
    assert asked(lambda p: p._fuzzy("Search", choices), [{"choice": None}])[0] is None
    assert asked(lambda p: p._fuzzy("Search", choices), [{"choice": 0}])[0] == 10
    assert asked(lambda p: p._checkbox("Tick", choices), [{"choices": [0, 1]}])[0] == [10, 20]


def test_the_real_questions_run_unchanged_on_the_page_prompter():
    """`confirm` and the file conflict are the terminal prompter's own code, answered by the page."""
    seen_choices = []

    def page(question, image=None):
        seen_choices.append(question)
        return {"choice": next(c["index"] for c in question["choices"] if c["key"] == "y")}
    assert WebPrompter(page).confirm("Sure?") is True
    assert seen_choices[0]["message"] == "Sure?" and {"y", "n", "a"} == {c["key"] for c in seen_choices[0]["choices"]}
    chosen, seen = asked(lambda p: p.conflict("/x.cbz", comicinfo.diff_fields({"Title": "Old"}, {"Title": "New"})), [{"choice": 0}])
    assert chosen == "database" and any(c["key"] == "w" for c in seen[0]["choices"])
    text, _ = asked(lambda p: p.ask_value("title", "Nains"), [{"text": " The title "}])
    assert text == "The title"
