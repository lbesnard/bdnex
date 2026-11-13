"""Going back to the previous question: what was decided from there is taken back, and asked again."""

import json
import os

import pytest

from bdnex.importer.session import GoBack, ImportAborted
from bdnex.importer.source import Candidate
from helpers import FakeWeb

from .test_session import Env, make_settings


def album(series, volume, ident):
    return Candidate(str(ident), f"https://www.bedetheque.com/BD-{series}-Tome-{volume}-Titre-{ident}.html",
                     f"{series.lower()} {volume} titre", volume)


FOO = [album("Foo", n, 100 + n) for n in (1, 2, 3)]


class Script:
    """Answers the questions in turn: an index (that candidate), ``None`` (skip), or an exception to raise."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def choose_match(self, path, matches):
        self.asked.append(os.path.basename(path))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception) or (isinstance(answer, type) and issubclass(answer, Exception)):
            raise answer
        return None if answer is None else matches[answer].candidate

    def duplicate_action(self, path, others, what):
        return "keep_both"


def env_for(prompter, auto=False, pretend=False, **settings):
    env = Env(FakeWeb(), make_settings(**{"import": {"auto": auto, "series": "never"}, **settings}), prompter,
              steps=("match",), pretend=pretend)
    env.source._index = list(FOO)
    env.source.is_ready = lambda: True
    return env


def files(make_cbz, *names):
    return [make_cbz(name, pages=3 + n) for n, name in enumerate(names)]


def volume_of(env, path):
    row = env.lib.get_file(path)
    return env.lib.get_album(row["album_id"])["volume"] if row and row["album_id"] else None


A, B, C = "Foo - T01 - Titre A.cbz", "Foo - T02 - Titre B.cbz", "Foo - T03 - Titre C.cbz"


def test_going_back_takes_the_previous_decision_back_and_asks_about_it_again(make_cbz):
    a, b = files(make_cbz, A, B)
    env = env_for(Script(0, GoBack, 1, 0))  # a: first candidate; b: back; a again: second candidate; b: first
    report = env.session.run([os.path.dirname(a)])
    assert env.session.prompter.asked == [os.path.basename(f) for f in (a, b, a, b)]
    assert len(report.results) == 2 and volume_of(env, a) is not None and volume_of(env, b) is not None
    assert env.session.prompter.answers == []  # every scripted answer was used
    assert volume_of(env, a) == 2  # a ends on the second candidate, the one chosen after going back (it began on volume 1)


def test_the_album_created_for_the_undone_decision_is_removed(make_cbz):
    a, b = files(make_cbz, A, B)
    env = env_for(Script(0, GoBack, None, None))  # a is picked, taken back, then skipped; b skipped
    env.session.run([os.path.dirname(a)])
    assert env.lib.get_file(a) is None or env.lib.get_file(a)["album_id"] is None
    assert env.lib.stats()["albums"] == 0 and env.lib.stats()["series"] == 0  # nothing of the first choice is left


def test_the_log_of_the_undone_decision_is_forgotten(make_cbz):
    a, b = files(make_cbz, A, B)
    env = env_for(Script(0, GoBack, None, None))
    env.session.run([os.path.dirname(a)])
    assert env.lib.last_import(a)["outcome"] == "skipped"  # the new decision, not the tagged/partial of the first


def test_a_skip_can_be_taken_back_too(make_cbz):
    a, b = files(make_cbz, A, B)
    env = env_for(Script(None, GoBack, 0, 0))
    env.session.run([os.path.dirname(a)])
    assert volume_of(env, a) is not None and env.lib.last_import(a)["outcome"] != "skipped"


def test_with_nothing_before_it_the_same_file_is_asked_again(make_cbz):
    (a,) = files(make_cbz, A)
    env = env_for(Script(GoBack, 0))
    messages = []
    env.session.progress = messages.append
    env.session.run([os.path.dirname(a)])
    assert env.session.prompter.asked == [os.path.basename(a)] * 2 and volume_of(env, a) is not None
    assert any("nothing before this file" in m for m in messages)


def test_files_matched_without_a_question_in_between_are_redone(make_cbz):
    # strong matches are applied unasked with auto; going back to the file before them redoes them
    a, b, c = files(make_cbz, "Foo Titre A.cbz", "Foo - T02 - Titre.cbz", "Foo Titre C.cbz")
    env = env_for(Script(0, GoBack, 0, 0), auto=True)
    env.source.candidates = lambda name, limit=5: [(FOO[1], 100.0 if "T02" in name or " 2 " in name else 60.0)] + [(FOO[0], 60.0)]
    report = env.session.run([os.path.dirname(a)])
    outcomes = [r.outcome for r in report.results]
    assert len(report.results) == 3 and "failed" not in outcomes
    assert all(env.lib.get_file(f) and env.lib.get_file(f)["album_id"] for f in (a, b, c))


def test_a_file_that_was_already_registered_stays_registered(make_cbz):
    a, b = files(make_cbz, A, B)
    env = env_for(Script(0, GoBack, None, None))
    env.lib.add_file(a)  # registered, not linked
    env.session.run([os.path.dirname(a)])
    assert env.lib.get_file(a) is not None  # taking the link back does not forget the file itself


def test_a_replaced_file_comes_back_when_the_decision_is_taken_back(make_cbz, tmp_path):
    old = make_cbz("Foo - T01 - Titre A.cbz", pages=3, directory=str(tmp_path / "library"))
    new = make_cbz("Foo - T01 - Titre A (autre).cbz", pages=5, directory=str(tmp_path / "downloads"))
    other = make_cbz("Foo - T02 - Titre B.cbz", pages=4, directory=str(tmp_path / "downloads"))

    class Prompter(Script):
        def resolve_duplicate(self, comparison, what, root=None):
            return "replace"

    env = env_for(Prompter(0), duplicates={"action": "ask"})
    env.session.run([old])  # the first copy of volume 1 is in the library
    assert volume_of(env, old) == 1
    # downloads: `new` replaces `old` (it goes to the trash); then `other` is asked about: back; `new` again: skipped
    env.session.prompter.answers = [0, GoBack, None, None]
    env.session.prompter.asked.clear()
    env.session.run([str(tmp_path / "downloads")])
    assert env.session.prompter.asked == [os.path.basename(f) for f in (new, other, new, other)]
    assert os.path.exists(old) and volume_of(env, old) == 1  # the old file came back, linked as before
    assert env.lib.trash_entries() == [] and env.lib.get_file(new) is None  # and nothing of the undone replacement is left


def test_aborting_after_a_replacement_puts_the_replaced_file_back(make_cbz):
    old, new = files(make_cbz, A, "Foo - T01 - Titre A (copie).cbz")
    import shutil
    shutil.copy(old, new)  # identical bytes: the "identical file" question

    class Prompter(Script):
        def resolve_duplicate(self, comparison, what, root=None):
            return "replace"

    env = env_for(Prompter(0, ImportAborted("x")), duplicates={"action": "ask"})
    env.session.run([old])
    assert volume_of(env, old) == 1
    report = env.session.run([new])  # the old copy is replaced, then the match question is aborted
    assert report.aborted_at == new
    assert os.path.exists(old) and env.lib.get_file(old) is not None and env.lib.trash_entries() == []


def test_a_preview_cannot_go_back_and_undoes_nothing(make_cbz):
    a, b = files(make_cbz, A, B)
    env = env_for(Script(GoBack), pretend=True)
    report = env.session.run([os.path.dirname(a)])
    assert report.aborted_at is None and len(report.results) == 2


def test_skipping_a_folder_skips_its_remaining_files_without_asking(make_cbz):
    from bdnex.importer.session import SkipFolder
    a, b, c = files(make_cbz, A, B, C)
    env = env_for(Script(SkipFolder))  # one question only: the other two files are never asked about
    report = env.session.run([os.path.dirname(a)])
    assert env.session.prompter.asked == [os.path.basename(a)]
    assert [r.outcome for r in report.results] == ["skipped"] * 3
    assert all("folder skipped" in r.note for r in report.results)
    assert env.lib.stats()["albums"] == 0


def test_going_back_after_a_folder_skip_asks_again(make_cbz):
    from bdnex.importer.session import SkipFolder
    a, b = files(make_cbz, A, B)
    env = env_for(Script(0, SkipFolder, GoBack, 0, 1))
    # a picked; b: skip folder... then nothing to go back from; scripted GoBack comes at the next question
    env.session.run([os.path.dirname(a)])
    assert volume_of(env, a) is not None
