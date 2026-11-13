"""Aborting an import: what is decided stays, nothing else starts, the next run resumes."""

import json

import pytest

from bdnex.importer.session import ALREADY, PARTIAL, SKIPPED, ImportAborted
from bdnex.ui.prompts import InquirerPrompter
from helpers import ALBUM_URL, FakeWeb

from .test_session import FILE, Bedetheque, Env, make_settings


class Aborter:
    """Takes the best match for the first files, then aborts at the n-th question."""

    def __init__(self, abort_at):
        self.abort_at, self.asked = abort_at, []

    def choose_match(self, path, matches):
        self.asked.append(path)
        if len(self.asked) == self.abort_at:
            raise ImportAborted(path)
        return matches[0].candidate

    def duplicate_action(self, path, others, what):
        return "skip"


def three_albums(make_cbz):
    return [make_cbz(f"Nains Redwin {n}.cbz", pages=3 + n) for n in range(3)]


def env_with(prompter):
    env = Env(FakeWeb(), make_settings(**{"import": {"auto": False, "series": "never"}, "duplicates": {"action": "keep_both"}}), prompter)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    return env


def test_aborting_keeps_what_was_decided_and_starts_nothing_else(make_cbz):
    files = three_albums(make_cbz)
    env = env_with(Aborter(abort_at=2))
    env.web.calls.clear()
    report = env.session.run(files)
    assert report.aborted_at == files[1] and report.total == 3 and len(report.results) == 1
    assert env.lib.get_file(files[0])["album_id"] is not None  # the first decision is saved
    assert env.lib.get_file(files[1]) is None and env.lib.get_file(files[2]) is None
    assert env.lib.last_import(files[1]) is None  # not logged, not "skipped": it is asked about again
    assert ALBUM_URL not in env.web.calls  # no page was fetched
    assert env.lib.get_album(report.results[0].album_id)["extra"].get("partial") is True  # matched only


def test_the_state_is_kept_in_the_library(make_cbz):
    files = three_albums(make_cbz)
    env = env_with(Aborter(abort_at=2))
    env.session.run(files)
    state = json.loads(env.lib.get_meta("import_aborted"))
    assert state == {"file": files[1], "done": 1, "total": 3, "paths": files}


def test_the_next_run_resumes_where_it_stopped_and_says_so(make_cbz):
    files = three_albums(make_cbz)
    env = env_with(Aborter(abort_at=2))
    first = env.session.run(files)
    messages = []
    env.session.progress = messages.append
    env.session.prompter = Aborter(abort_at=99)  # nobody aborts this time
    report = env.session.run(files)
    assert any("Resuming: the last import was aborted at" in m and "Nains Redwin 1.cbz" in m for m in messages)
    assert env.lib.get_meta("import_aborted") is None  # the record is cleared
    assert env.session.prompter.asked == [files[1], files[2]]  # the first file is not asked about again
    assert all(env.lib.get_file(f)["album_id"] is not None for f in files)
    # the file decided before the abort still only had its sitemap data: this run read its page
    assert first.results[0].outcome == PARTIAL and report.results[0].outcome in (ALREADY, "tagged", PARTIAL)
    assert ALBUM_URL in env.web.calls


def test_aborting_again_replaces_the_record(make_cbz):
    files = three_albums(make_cbz)
    env = env_with(Aborter(abort_at=2))
    env.session.run(files)
    env.session.prompter = Aborter(abort_at=1)
    env.session.run(files)
    assert json.loads(env.lib.get_meta("import_aborted"))["file"] == files[1]


def test_a_finished_run_leaves_no_record(make_cbz):
    files = three_albums(make_cbz)
    env = env_with(Aborter(abort_at=99))
    env.session.run(files)
    assert env.lib.get_meta("import_aborted") is None


def test_a_preview_cannot_abort_and_writes_nothing(make_cbz):
    files = three_albums(make_cbz)
    env = env_with(Aborter(abort_at=1))
    env.session.pretend = True
    report = env.session.run(files)
    assert report.aborted_at is None and env.lib.get_meta("import_aborted") is None


def test_the_match_question_offers_to_abort_and_aborting_raises(monkeypatch):
    class P(InquirerPrompter):
        def _need_terminal(self):
            pass

        def _select(self, message, choices, fragments=None, shortcuts=None):
            self.choices = choices
            return InquirerPrompter.ABORT

    prompter = P()
    with pytest.raises(ImportAborted):
        prompter.choose_match("/c/a.cbz", [])
    names = [c["name"] for c in prompter.choices]
    assert names[-4] == "[S]kip this file" and names[-1].startswith("[A]bort the import")
