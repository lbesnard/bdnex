"""Watching the import sources: an automatic, question-free import, only when it is wanted and possible."""

import io

import pytest
import yaml

pytest.importorskip("fastapi")

from api.test_web_features import env, wait  # noqa: E402,F401
from bdnex.api import jobs  # noqa: E402
from bdnex.api.watch import FIRST_LOOK, POLL, Watcher  # noqa: E402
from bdnex.config import build_settings  # noqa: E402
from bdnex.ui import import_cmd  # noqa: E402


def watching(settings, tmp_path, enabled=True, sources=True, interval=15):
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    user = {"config_version": 2, "collection_path": str(settings.collection_path), "library": str(settings.library_path),
            "watch": {"enabled": enabled, "interval": interval}}
    if sources:
        user["import"] = {"sources": [{"path": str(inbox), "mode": "copy", "name": "inbox"}]}
    return build_settings(user)[0]


def test_nothing_starts_unless_watching_is_on_and_there_is_a_source(env, tmp_path):
    settings, book, album = env
    off = watching(settings, tmp_path, enabled=False)
    assert Watcher(off, jobs.JobManager(off, store=tmp_path / "h1.json")).look() is False
    nothing = watching(settings, tmp_path, sources=False)
    assert Watcher(nothing, jobs.JobManager(nothing, store=tmp_path / "h2.json")).look() is False


def test_a_look_starts_a_question_free_import_of_the_sources(env, tmp_path):
    settings, book, album = env
    on = watching(settings, tmp_path)
    manager = jobs.JobManager(on, store=tmp_path / "h.json")
    watcher = Watcher(on, manager)
    assert watcher.look() is True
    job = manager.get(watcher.last_job)
    assert job.command == "watch" and job.interactive is False
    assert job.argv == ["--unattended", "--sources"]  # no "-n": a real run, and nothing asked
    wait_for(manager, job.id)
    assert manager.get(job.id).status in ("done", "failed")  # (there is no album list here: it says so and stops)


def wait_for(manager, job_id):
    import time
    for _ in range(200):
        if manager.get(job_id).status != "running":
            return
        time.sleep(0.05)
    raise AssertionError("the watch did not finish")


def test_a_look_is_skipped_while_another_task_runs(env, tmp_path, monkeypatch):
    settings, book, album = env
    on = watching(settings, tmp_path)
    import threading
    release = threading.Event()
    monkeypatch.setitem(jobs.SPECS, "slow", jobs.Spec("slow", False, lambda options, settings: (lambda out: release.wait(5) and 0, [])))
    manager = jobs.JobManager(on, store=tmp_path / "h.json")
    running = manager.start("slow", {})
    assert Watcher(on, manager).look() is False  # busy: the next look will do
    release.set()
    wait_for(manager, running.id)


def test_the_loop_looks_after_the_first_delay_then_by_the_interval_and_re_reads_the_settings(env, tmp_path):
    settings, book, album = env
    on = watching(settings, tmp_path, interval=2)
    looks, waits = [], []

    class Fake(Watcher):
        def look(self):
            looks.append(len(waits))
            return False

    def wait(seconds):
        waits.append(seconds)
        return len(waits) > 20  # stop after a while

    watcher = Fake(on, jobs.JobManager(on, store=tmp_path / "h.json"), wait=wait)
    watcher.run()
    assert all(w <= POLL for w in waits)
    assert looks[0] * POLL >= FIRST_LOOK - POLL and looks[0] * POLL <= FIRST_LOOK + POLL  # the first look is soon
    gap = (looks[1] - looks[0]) * POLL
    assert 2 * 60 - POLL <= gap <= 2 * 60 + POLL  # then every `watch.interval` minutes


def test_the_watch_job_is_not_listed_for_the_page_but_runs_like_any_task(env):
    settings, book, album = env
    assert "watch" in jobs.SPECS and jobs.SPECS["watch"].listed is False


def test_the_job_needs_a_source_and_an_import_can_be_unattended_or_only_the_skipped(env, tmp_path):
    settings, book, album = env
    with pytest.raises(jobs.JobError, match="no import source"):
        jobs.SPECS["import"].build({"sources": True, "preview": False, "unattended": True}, settings)
    on = watching(settings, tmp_path)
    _, argv = jobs.SPECS["import"].build({"sources": True, "preview": False, "unattended": True, "skipped": True}, on)
    assert argv == ["--unattended", "--sources", "--skipped"]


def test_unattended_import_asks_nothing_and_leaves_the_unsure_files_for_you(tmp_path, monkeypatch):
    """`--unattended` gives the session no prompter: a file that is not a confident match is skipped, never asked."""
    captured = {}

    class Session:
        def __init__(self, library, source, settings, prompter=None, **kw):
            captured["prompter"] = prompter

        def run(self, paths):
            raise SystemExit(0)

    conf = tmp_path / "c.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "collection_path": str(tmp_path / "comics"), "library": str(tmp_path / "l.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    (tmp_path / "comics").mkdir()
    monkeypatch.setattr(import_cmd, "ImportSession", Session)
    monkeypatch.setattr(import_cmd, "ensure_album_list", lambda *a, **k: True)
    monkeypatch.setattr(import_cmd, "create_source", lambda *a, **k: type("S", (), {"name": "bedetheque", "is_ready": lambda s: True})())
    for flags, expected in (([], "asks"), (["--unattended"], None)):
        captured.clear()
        with pytest.raises(SystemExit):
            import_cmd.run([*flags, str(tmp_path / "comics")], out=io.StringIO())
        assert (captured["prompter"] is None) == (expected is None)
