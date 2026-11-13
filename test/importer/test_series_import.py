"""A folder with several files: assumed a series when obvious, asked when unclear, never when told not to."""

import os

import pytest

from bdnex.importer.session import NeedsTerminal, ImportAborted
from bdnex.importer.source import Candidate
from helpers import FakeWeb

from .test_session import Env, make_settings


def album(series, volume, ident):
    return Candidate(str(ident), f"https://www.bedetheque.com/BD-{series}-Tome-{volume}-Titre-{ident}.html",
                     f"{series.lower()} {volume} titre", volume)


FOO = {n: album("Foo", n, 100 + n) for n in (1, 2, 3)}
BAR = {n: album("Bar", n, 200 + n) for n in (1, 2, 3)}


class Prompter:
    """Records its questions; answers the series question as told and takes the first match otherwise."""

    def __init__(self, series_answer="series"):
        self.series_answer, self.series_asked, self.match_asked = series_answer, [], []

    def confirm_series(self, folder, paths, proposal):
        self.series_asked.append((folder, [os.path.basename(p) for p in paths], proposal))
        if isinstance(self.series_answer, Exception):
            raise self.series_answer
        return self.series_answer

    def choose_match(self, path, matches):
        self.match_asked.append(os.path.basename(path))
        return matches[0].candidate if matches else None

    def duplicate_action(self, path, others, what):
        return "keep_both"


def env_for(prompter, mode="ask", **kw):
    settings = make_settings(**{"import": {"auto": False, "series": mode}, "duplicates": {"action": "keep_both"}})
    env = Env(FakeWeb(), settings, prompter, steps=("match",), **kw)
    env.source._index = [*FOO.values(), *BAR.values()]
    env.source.is_ready = lambda: True
    return env


def folder(make_cbz, name, *files):
    return [make_cbz(file, pages=3 + n, directory=os.path.join(os.path.dirname(make_cbz("x.cbz")), name))
            for n, file in enumerate(files)]


def linked(env, path):
    row = env.lib.get_file(path)
    album_row = env.lib.get_album(row["album_id"]) if row and row["album_id"] else None
    return (album_row["series"], album_row["volume"]) if album_row else None


OBVIOUS = ("Foo - T01 - Titre.cbz", "Foo - T02 - Titre.cbz", "Foo - T03 - Titre.cbz")
UNCLEAR = (*OBVIOUS[:2], "Foo - Special.cbz")


def test_an_obvious_series_is_assumed_without_a_question(make_cbz):
    files = folder(make_cbz, "Foo", *OBVIOUS)
    env = env_for(Prompter())
    messages = []
    env.session.progress = messages.append
    env.session.run([os.path.dirname(files[0])])
    assert env.session.prompter.series_asked == [] and env.session.prompter.match_asked == []  # nobody was asked anything
    assert [linked(env, f) for f in files] == [("Foo", 1), ("Foo", 2), ("Foo", 3)]
    assert any("Series folder Foo: 3 files, 3 found by volume in \"Foo\"" in m for m in messages)
    assert any("(series \"Foo\")" in m for m in messages)


def test_an_unclear_folder_asks_and_yes_maps_the_files_by_volume(make_cbz):
    files = folder(make_cbz, "Foo", *UNCLEAR)
    env = env_for(Prompter("series"))
    env.session.run([os.path.dirname(files[0])])
    (folder_name, shown, proposal), = env.session.prompter.series_asked
    assert folder_name == "Foo" and shown == sorted(os.path.basename(f) for f in files) and proposal.name == "Foo"
    assert linked(env, files[0]) == ("Foo", 1) and linked(env, files[1]) == ("Foo", 2)
    assert env.session.prompter.match_asked == ["Foo - Special.cbz"]  # only the file without a volume is matched alone


def test_no_means_each_file_is_matched_by_itself(make_cbz):
    files = folder(make_cbz, "Foo", *UNCLEAR)
    env = env_for(Prompter("each"))
    env.session.run([os.path.dirname(files[0])])
    assert len(env.session.prompter.series_asked) == 1
    assert len(env.session.prompter.match_asked) == 3  # every file went through the usual question
    assert all(linked(env, f) is not None for f in files)


def test_series_always_assumes_it_without_asking(make_cbz):
    files = folder(make_cbz, "Foo", *UNCLEAR)
    env = env_for(Prompter("each"), mode="always")
    env.session.run([os.path.dirname(files[0])])
    assert env.session.prompter.series_asked == [] and linked(env, files[0]) == ("Foo", 1)


def test_series_never_matches_every_file_alone_and_asks_nothing(make_cbz):
    files = folder(make_cbz, "Foo", *OBVIOUS)
    env = env_for(Prompter(), mode="never")
    env.session.run([os.path.dirname(files[0])])
    assert env.session.prompter.series_asked == [] and sorted(env.session.prompter.match_asked) == sorted(os.path.basename(f) for f in files)


def test_versions_of_one_book_are_not_a_series(make_cbz):
    files = folder(make_cbz, "Foo", "Foo - T01 - Titre.cbz", "Foo - T01 - Titre (N&B).cbz")
    env = env_for(Prompter())
    env.session.run([os.path.dirname(files[0])])
    assert env.session.prompter.series_asked == []


def test_a_lone_file_is_never_a_folder(make_cbz):
    files = folder(make_cbz, "Foo", "Foo - T01 - Titre.cbz")
    env = env_for(Prompter())
    env.session.run([os.path.dirname(files[0])])
    assert env.session.prompter.series_asked == []


@pytest.mark.parametrize("kwargs", [{"pretend": True}, {"auto": "all"}])
def test_nobody_is_asked_in_a_preview_or_with_auto_all(make_cbz, kwargs):
    files = folder(make_cbz, "Foo", *UNCLEAR)
    env = env_for(Prompter("series"), **kwargs)
    env.session.run([os.path.dirname(files[0])])
    assert env.session.prompter.series_asked == []


def test_without_a_terminal_the_folder_falls_back_to_one_by_one(make_cbz):
    files = folder(make_cbz, "Foo", *UNCLEAR)
    env = env_for(Prompter(NeedsTerminal("no terminal")))
    report = env.session.run([os.path.dirname(files[0])])
    assert len(report.results) == 3 and report.aborted_at is None


def test_a_prompter_that_cannot_ask_about_series_is_fine(make_cbz):
    class Old:
        def choose_match(self, path, matches):
            return matches[0].candidate if matches else None

        def duplicate_action(self, path, others, what):
            return "skip"

    files = folder(make_cbz, "Foo", *UNCLEAR)
    env = env_for(Old())
    assert len(env.session.run([os.path.dirname(files[0])]).results) == 3


def test_aborting_at_the_series_question_stops_the_import(make_cbz):
    files = folder(make_cbz, "Foo", *UNCLEAR)
    env = env_for(Prompter(ImportAborted("Foo")))
    report = env.session.run([os.path.dirname(files[0])])
    assert report.aborted_at == sorted(files)[0] and report.results == []


def test_each_folder_is_decided_on_its_own(make_cbz):
    foo = folder(make_cbz, "Foo", *OBVIOUS)
    bar = folder(make_cbz, "Bar", *("Bar - T01 - Titre.cbz", "Bar - T02 - Titre.cbz"))
    env = env_for(Prompter())
    root = os.path.dirname(os.path.dirname(foo[0]))
    env.session.run([root])
    assert [linked(env, f) for f in foo] == [("Foo", 1), ("Foo", 2), ("Foo", 3)]
    assert [linked(env, f) for f in bar] == [("Bar", 1), ("Bar", 2)]


def test_ticking_files_makes_the_others_the_next_series(make_cbz):
    names = ("Foo - T01 - Titre.cbz", "Foo - T02 - Titre.cbz", "Bar - T01 - Titre.cbz", "Bar - T02 - Titre.cbz")

    class Choosing(Prompter):
        def __init__(self):
            super().__init__()
            self.answers = ["choose", "series"]
            self.picks = []

        def confirm_series(self, folder, paths, proposal):
            super().confirm_series(folder, paths, proposal)
            return self.answers.pop(0)

        def pick_files(self, folder, paths, ticked):
            self.picks.append([os.path.basename(p) for p in paths])
            return [p for p in paths if os.path.basename(p).startswith("Foo")]

    files = folder(make_cbz, "Mixed", *names)
    env = env_for(Choosing())
    env.session.run([os.path.dirname(files[0])])
    prompter = env.session.prompter
    assert len(prompter.picks) == 1 and len(prompter.series_asked) == 2  # asked again for the files left over
    assert [linked(env, f) for f in files] == [("Foo", 1), ("Foo", 2), ("Bar", 1), ("Bar", 2)]


def test_ticking_nothing_matches_each_file_by_itself_and_ends(make_cbz):
    class Nothing(Prompter):
        def confirm_series(self, folder, paths, proposal):
            super().confirm_series(folder, paths, proposal)
            return "choose"

        def pick_files(self, folder, paths, ticked):
            return []

    files = folder(make_cbz, "Foo", *UNCLEAR)
    env = env_for(Nothing())
    env.session.run([os.path.dirname(files[0])])
    assert len(env.session.prompter.series_asked) == 1 and len(env.session.prompter.match_asked) == 3
