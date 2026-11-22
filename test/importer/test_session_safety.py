"""Findings of the review of the import session: a read-only source is never touched, Ctrl-C leaves nothing half done, a preview changes nothing."""

import os
import time

import pytest

from bdnex.importer import pipeline
from bdnex.importer.session_types import ImportReport, ImportResult
from bdnex.importer.source import Candidate

from .test_back import Script, env_for, files, volume_of


def test_a_file_in_a_read_only_source_is_never_trashed_as_a_replaced_file(make_cbz, tmp_path):
    from .test_session import Env, make_settings
    from helpers import FakeWeb

    source = tmp_path / "nas"
    source.mkdir()
    kept = make_cbz("old.cbz", directory=source)
    env = Env(FakeWeb(), make_settings(**{"import": {"sources": [{"path": str(source), "mode": "copy"}]}}), None, steps=("match",))
    env.lib.add_file(kept)
    env.session._trash_others([kept], "/x/new.cbz")  # `duplicates.action: replace`, or the person chose Replace
    assert os.path.exists(kept) and env.lib.get_file(kept) is not None  # not moved, not forgotten
    elsewhere = make_cbz("other.cbz", directory=tmp_path / "plain")
    env.lib.add_file(elsewhere)
    env.session._trash_others([elsewhere], "/x/new.cbz")
    assert not os.path.exists(elsewhere)  # a file of the collection is trashed as before


def test_going_back_takes_the_cover_check_of_that_result_with_it():
    from bdnex.importer.session import ImportSession

    session = ImportSession.__new__(ImportSession)
    session._pending, session._done, session._undone, session.pretend = [], set(), [], True
    gone, kept = ImportResult("/x/a.cbz", "tagged"), ImportResult("/x/b.cbz", "tagged")
    session._covers, session._cover_jobs = [gone, kept], [(gone, object()), (kept, object())]
    session._undo(gone)
    assert session._covers == [kept] and [job[0] for job in session._cover_jobs] == [kept]  # it used to be compared with the album chosen next


def test_ctrl_c_during_the_covers_stops_the_run_and_the_report_says_so(make_cbz):
    env = env_for(Script(), pretend=False)
    session = env.session
    session._covers = [ImportResult("/x/a.cbz", "tagged")]
    session._queue_cover = lambda result: session._cover_jobs.append((result, object()))

    def interrupted(final=False, tick=None):
        raise KeyboardInterrupt

    session._take_covers = interrupted
    session._check_covers()
    assert session._stopped is True  # so no cover vote runs afterwards (its requests are refused: it would end in a traceback)
    assert ImportReport().stopped is False


def test_ctrl_c_while_the_cover_vote_applies_its_choice_takes_the_half_done_link_back(make_cbz):
    (a,) = files(make_cbz, "Foo - T01 - Titre A.cbz")
    env = env_for(Script())
    session, undone = env.session, []
    session._wants_vote = lambda: True
    session._vote = lambda path, matches: (Candidate("1", "u", "n", 1), 90.0, 80.0)
    session._import_matched = lambda *args, **kw: (_ for _ in ()).throw(KeyboardInterrupt())
    session._undo_journal = lambda journal, path: undone.append(path)
    session._cover_votes_waiting = None
    session._undecided = [(a, [])]
    session._cover_votes(ImportReport())
    assert undone == [a]  # as `import_file` does: nothing half done stays behind (it used to be undone for an Exception only)


def test_a_preview_without_a_library_creates_nothing_and_with_one_changes_nothing(tmp_path, monkeypatch, make_cbz):
    import io

    import yaml

    from bdnex.library import Library
    from bdnex.ui import import_cmd

    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    path = make_cbz("a.cbz")
    assert import_cmd.run(["-n", path], out=io.StringIO()) == 1 and not db.exists()  # nothing to preview, and nothing created
    Library(db).close()
    before = db.read_bytes()
    import_cmd.run(["-n", path], out=io.StringIO())
    assert db.read_bytes() == before  # a preview does not even touch the file (no migration, no path rewriting, no backup)
    assert not list(tmp_path.glob("*.bak*"))


def test_a_file_gone_from_the_disk_is_not_a_duplicate_of_the_same_bytes_elsewhere(make_cbz, tmp_path):
    from .test_session import Env, make_settings
    from helpers import FakeWeb

    env = Env(FakeWeb(), make_settings(), None, steps=("match",))
    old = make_cbz("old.cbz")
    env.lib.add_file(old)
    new = str(tmp_path / "moved.cbz")
    os.replace(old, new)  # moved outside bdnex: the row of `old` is a ghost
    assert env.session._duplicate_file(new) is None  # it used to be skipped as 'identical to <missing path>'


def test_a_spare_file_linked_again_where_it_is_alone_is_no_longer_a_spare(make_cbz):
    from .test_session import Env, make_settings
    from helpers import FakeWeb

    (a,) = files(make_cbz, "Foo - T01 - Titre A.cbz")
    env = Env(FakeWeb(), make_settings(**{"import": {"auto": False, "series": "never"}}), Script(0, 0), steps=("match",), force=True)
    from .test_back import FOO

    env.source._index = list(FOO)
    env.source.is_ready = lambda: True
    env.session.import_file(a)
    env.lib.set_file_fields(a, spare=1)  # it was a spare version of another album
    env.session.import_file(a)  # --force: linked again, alone on its album
    assert env.lib.get_file(a)["spare"] == 0


# ------------------------------------------------------------------ the matching workers
class SlowSource:
    def candidates(self, text, limit=5):
        return []


def test_the_workers_are_forked_when_matching_starts_not_at_the_first_file(monkeypatch):
    import multiprocessing

    stream = pipeline.match_ahead(SlowSource(), [f"/x/{n}.cbz" for n in range(4)], workers=2, min_parallel=1)
    try:
        assert multiprocessing.active_children()  # before the first `next`: no thread of the caller exists yet at the fork
    finally:
        stream.close()


def test_a_worker_that_never_answers_fails_its_file_instead_of_hanging_the_import(monkeypatch):
    monkeypatch.setattr(pipeline, "WORKER_TIMEOUT", 0.3)
    monkeypatch.setattr(pipeline, "find_matches", lambda source, path: time.sleep(5) or [])
    stream = pipeline.match_ahead(SlowSource(), ["/x/a.cbz", "/x/b.cbz"], workers=2, min_parallel=1)
    try:
        path, found = next(stream)
        assert path == "/x/a.cbz" and isinstance(found, TimeoutError) and "did not answer" in str(found)
    finally:
        stream.close()
