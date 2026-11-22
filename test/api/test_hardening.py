"""What the review of the web API found: rebinding, the forwarded address, previews, cancels, the config file's mode, the API docs."""

import os
import stat
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from api.test_web_settings import conf, env  # noqa: E402,F401
from bdnex.api import jobs, settings_api  # noqa: E402
from bdnex.api.app import create_app  # noqa: E402
from bdnex.config import build_settings  # noqa: E402


def settings_for(tmp_path, **api):
    user = {"config_version": 2, "collection_path": str(tmp_path / "comics"), "library": str(tmp_path / "lib.sqlite")}
    if api:
        user["api"] = api
    return build_settings(user)[0]


def named(settings, name, **kw):
    return TestClient(create_app(settings, **kw), base_url=f"http://{name}")


# ------------------------------------------------------------------------------------------------ DNS rebinding
@pytest.mark.parametrize("name, status", [("localhost:8765", 200), ("127.0.0.1:8765", 200), ("[::1]:8765", 200), ("localhost", 200),
                                          ("evil.example:8765", 400), ("evil.example", 400)])
def test_a_server_on_this_machine_answers_only_to_this_machines_names(tmp_path, name, status):
    assert named(settings_for(tmp_path), name).get("/healthz").status_code == status


def test_allowed_hosts_add_names_and_a_reachable_server_answers_to_any_name_unless_they_are_listed(tmp_path):
    local = settings_for(tmp_path, allowed_hosts=["bdnex.home"])
    assert named(local, "bdnex.home:8765").get("/healthz").status_code == 200 and named(local, "evil.example").get("/healthz").status_code == 400
    assert named(local, "localhost").get("/healthz").status_code == 200  # its own names stay
    open_server = settings_for(tmp_path, host="0.0.0.0")
    assert named(open_server, "anything.example").get("/healthz").status_code == 200  # it cannot know its names
    fenced = settings_for(tmp_path, host="0.0.0.0", allowed_hosts=["bdnex.home"])
    assert named(fenced, "bdnex.home").get("/healthz").status_code == 200 and named(fenced, "evil.example").get("/healthz").status_code == 400


# ------------------------------------------------------------------------------------------------ the forwarded address
def test_behind_a_proxy_the_last_forwarded_address_is_the_client_so_a_spoofed_first_one_does_not_reset_the_lockout(tmp_path):
    settings = settings_for(tmp_path, behind_proxy=True)
    web = named(settings, "localhost", password="s3cret", username="me")
    wrong = {"Authorization": "Basic bWU6d3Jvbmc="}  # me:wrong
    statuses = [web.get("/api/health", headers={**wrong, "X-Forwarded-For": f"10.0.0.{n}, 203.0.113.9"}).status_code for n in range(7)]
    assert statuses[:5] == [401] * 5 and statuses[5:] == [429, 429]  # one client (the proxy's last entry), however it signs itself


# ------------------------------------------------------------------------------------------------ the body
def test_a_chunked_body_and_a_bad_length_are_refused_not_buffered(tmp_path):
    web = named(settings_for(tmp_path), "localhost")
    assert web.post("/api/login", content=b"{}", headers={"Transfer-Encoding": "chunked", "Content-Length": "2"}).status_code == 411
    assert web.post("/api/login", content=b"{}", headers={"Content-Length": "abc"}).status_code == 400


# ------------------------------------------------------------------------------------------------ the API docs
def test_the_schema_and_the_docs_need_the_login_like_everything_else(tmp_path):
    web = named(settings_for(tmp_path), "localhost", password="s3cret", username="me")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert web.get(path).status_code == 401, path
    basic = {"Authorization": "Basic bWU6czNjcmV0"}  # me:s3cret
    assert web.get("/openapi.json", headers=basic).status_code == 200 and "/api/albums" in web.get("/openapi.json", headers=basic).json()["paths"]
    assert web.get("/docs", headers=basic).status_code == 200


# ------------------------------------------------------------------------------------------------ jobs
def test_check_fix_as_a_preview_runs_the_check_that_changes_nothing(tmp_path):
    spec = jobs.SPECS["check-fix"]
    assert spec.build({"preview": True}, None)[1] == []  # `check` alone: the command has no -n, this is its preview
    assert spec.build({"preview": False}, None)[1] == ["--fix"]
    assert spec.build({}, None)[1] == []  # a preview is the default


def test_a_batch_name_cannot_be_a_flag():
    spec = jobs.SPECS["undo"]
    for flag in ("--yes", "-n", "-"):
        with pytest.raises(jobs.JobError):
            spec.build({"batch": flag}, None)
    assert "last" in spec.build({"batch": "last"}, None)[1]


def test_a_cancel_is_refused_once_the_run_is_over_and_a_late_one_cannot_leave_the_slot_taken(tmp_path):
    manager = jobs.JobManager(settings_for(tmp_path), store=tmp_path / "jobs.json")
    job = jobs.Job(1, "check", "Check", [])
    job.thread_id = threading.get_ident()
    manager.jobs[1] = job
    job.finishing = True
    assert manager.cancel(1) is False  # nothing is injected into a thread that is putting things away

    class Log:
        calls = 0

        def flush(self):
            Log.calls += 1
            if Log.calls == 1:
                raise KeyboardInterrupt  # a cancel that was already on its way when the run ended

    assert manager._running.acquire(blocking=False)
    job.finishing = False
    manager._finish(job, Log(), "done")
    assert job.status == "done" and job.finished  # the status is not left at "running"
    assert manager._running.acquire(blocking=False)  # and the slot was freed (it is not "busy" for ever)


# ------------------------------------------------------------------------------------------------ the config file
def test_the_settings_editor_keeps_the_mode_of_the_file_and_gives_its_copy_the_same(env, conf):
    settings, book, album = env
    conf.chmod(0o600)  # it holds passwords and tokens
    settings_api.change(settings, conf, {"write.pdf": "tag"}, apply=True)
    assert "pdf: tag" in conf.read_text()
    for path in (conf, conf.with_name(conf.name + ".bak")):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, path
    assert not conf.with_name(conf.name + ".tmp").exists()
