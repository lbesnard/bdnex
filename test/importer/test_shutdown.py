"""However a run ends (a question aborted, Ctrl-C, a failure), what runs in the background is closed, and a stop means stop."""

import os

import pytest

from bdnex.importer.session import ImportAborted, NeedsTerminal
from bdnex.importer.session_types import ImportReport

from .test_back import A, B, Script, env_for, files


class Stopper(Script):
    """Like `Script`, but the exception may be a `BaseException` (Ctrl-C)."""

    def choose_match(self, path, matches):
        answer = self.answers[0]
        if isinstance(answer, type) and issubclass(answer, BaseException):
            self.answers.pop(0)
            self.asked.append(os.path.basename(path))
            raise answer
        return super().choose_match(path, matches)


def _record_drops(env):
    calls = []
    real = env.session._drop_covers
    env.session._drop_covers = lambda: (calls.append(1), real())[1]
    return calls


def test_aborting_drops_the_covers_the_finishing_of_the_stored_pages_queued(make_cbz):
    a, b = files(make_cbz, A, B)
    env = env_for(Script(0, ImportAborted))
    calls = _record_drops(env)
    report = env.session.run([os.path.dirname(a)])
    assert report.aborted_at and calls  # the cover threads are told to stop, not left to wait out their polite delay


@pytest.mark.parametrize("stop", [NeedsTerminal, KeyboardInterrupt])
def test_a_question_that_cannot_be_asked_or_ctrl_c_drops_the_covers_and_the_background_work(make_cbz, stop):
    a, b = files(make_cbz, A, B)
    env = env_for(Stopper(0, stop))
    calls = _record_drops(env)
    with pytest.raises(stop):
        env.session.run([os.path.dirname(a)])
    assert calls and env.session._early is None


def test_a_failure_outside_a_files_own_work_closes_the_background_work_too(make_cbz):
    (a,) = files(make_cbz, A)
    env = env_for(Script(0))
    calls = _record_drops(env)
    closed = []
    env.session._attention = lambda result: (_ for _ in ()).throw(RuntimeError("boom"))  # runs outside the per-file try
    real_open = env.session._open_early

    def opened():
        real_open()
        env.session._early = type("Early", (), {"close": lambda self: closed.append(1), "store": lambda self: []})()

    env.session._open_early = opened
    with pytest.raises(RuntimeError, match="boom"):
        env.session.run([os.path.dirname(a)])
    assert calls and closed and env.session._early is None


def test_ctrl_c_while_fetching_means_no_cover_vote_afterwards(make_cbz):
    (a,) = files(make_cbz, A)
    env = env_for(Script(0))
    voted = []
    env.session._cover_votes = lambda report: voted.append(1)
    env.session._drain_early = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    env.session.steps = ("match", "fetch")
    env.session.run([os.path.dirname(a)])
    assert env.session._stopped and voted == []
    env2 = env_for(Script(0))
    env2.session._cover_votes = lambda report: voted.append(2)
    env2.session.run([os.path.dirname(a)])
    assert voted == [2]  # without a Ctrl-C the vote pass still runs


def test_a_failing_vote_is_undone_and_the_pass_goes_on_with_the_next_file(make_cbz):
    a, b = files(make_cbz, A, B)
    env = env_for(Script())
    session = env.session
    session._undecided = [(a, []), (b, [])]
    session._wants_vote = lambda: True
    winners = {a: (list(env.source._index)[0], 90.0, 80.0), b: (list(env.source._index)[1], 90.0, 80.0)}
    session._vote = lambda path, matches: winners[path]
    undone = []
    session._undo_journal = lambda journal, path: undone.append(path)

    def import_matched(path, matches, defer, chosen):
        if path == a:
            session._journal.linked = True  # half done, then the failure
            raise RuntimeError("the disk said no")
        from bdnex.importer.session_types import ImportResult
        return ImportResult(path, "tagged", score=90.0)

    session._import_matched = import_matched
    report = ImportReport()
    session._cover_votes(report)  # does not raise
    assert undone == [a] and [r.path for r in report.results] == [b]
