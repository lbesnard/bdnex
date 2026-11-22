"""Findings of the review of the prompts: data is never key markup, nothing is asked without a terminal, colour and width follow the terminal."""

import io
import logging

import pytest

from bdnex.ui import color, keys, output
from bdnex.ui.prompts import InquirerPrompter, NeedsTerminal, SkipFolder


def test_a_title_with_brackets_is_text_not_a_key():
    entries = [{"action": "database", "value": "db", "label": "Database: Tome [2]", "key": "d"},
               {"action": "file", "value": "file", "label": "File:     Tome [x]", "key": "f"},
               {"action": "skip", "value": "skip"}]
    choices, fragments, shortcuts = keys.build(entries, True, lambda key, rest, tone: [(tone, key), (tone, rest)])
    assert set(shortcuts) >= {"d", "f", "s"} and "2" not in shortcuts and "x" not in shortcuts  # no key taken from the album's own text
    assert choices[0]["name"].startswith("[D]atabase: Tome [2]")


def test_the_progress_bar_honours_no_color(monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setenv("NO_COLOR", "1")
    bar = output.Bar(10, "x", stream=Terminal(), tty=True)
    bar.done = 3
    assert "\x1b[" not in bar.line("detail")
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert "\x1b[" in bar.line("detail")


def test_a_line_is_cut_by_terminal_columns_not_characters(monkeypatch):
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=None: type("S", (), {"columns": 21})())
    bar = output.Bar(10, "", stream=io.StringIO(), tty=True)
    fitted = bar._fit("漢字" * 20)  # every character takes two columns
    assert output._cells(fitted) <= 20 and fitted.endswith("…")
    assert output._cells("é") == 1  # a combining accent takes none


@pytest.mark.parametrize("value,expected", [("0", False), ("false", False), ("1", True), ("true", True)])
def test_force_color_zero_means_no_colour(monkeypatch, value, expected):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", value)
    assert color.enabled(io.StringIO()) is expected


def test_a_log_line_has_the_same_text_with_and_without_colour():
    record = logging.LogRecord("bdnex.x", logging.INFO, "f", 1, "hello", None, None)
    plain = color.LogFormatter("%(levelname)s %(name)s: %(message)s", False).format(record)
    painted = color.LogFormatter("%(levelname)s %(name)s: %(message)s", True).format(record)
    import re

    assert re.sub(r"\x1b\[[0-9;]*m", "", painted) == plain


def test_setting_up_logging_twice_leaves_one_handler_of_ours_and_the_others_alone():
    root = logging.getLogger()
    other = logging.NullHandler()
    root.addHandler(other)
    try:
        output.setup_logging(1)
        output.setup_logging(1)
        assert sum(1 for h in root.handlers if getattr(h, "_bdnex", False)) == 1 and other in root.handlers
    finally:
        root.removeHandler(other)
        for h in [h for h in root.handlers if getattr(h, "_bdnex", False)]:
            root.removeHandler(h)


def test_a_question_without_a_terminal_says_so(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO())  # not a tty
    with pytest.raises(NeedsTerminal):
        InquirerPrompter()._text("Album Id?")
    with pytest.raises(NeedsTerminal):
        InquirerPrompter().confirm("Sure?")


def test_cancelling_the_file_ticks_skips_the_folder_instead_of_saying_none_belong():
    prompter = InquirerPrompter()
    prompter._need_terminal = lambda: None
    prompter._checkbox = lambda message, choices: None  # Ctrl-C
    with pytest.raises(SkipFolder):
        prompter.pick_files("folder", ["/a.cbz", "/b.cbz"], set())
    prompter._checkbox = lambda message, choices: ["/a.cbz"]
    assert prompter.pick_files("folder", ["/a.cbz", "/b.cbz"], set()) == ["/a.cbz"]


def test_an_album_id_the_source_cannot_look_up_is_said_and_asked_again(capsys):
    class Source:
        def __init__(self):
            self.calls = 0

        def candidate_for(self, text):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("index not loaded")
            return "found"

    prompter = InquirerPrompter(source=Source())
    answers = iter(["123", "12 34", "456"])
    prompter._text = lambda message, default="": next(answers)
    assert prompter.ask_id() == "found"  # "12 34" never reached the source; the failing first lookup did not end the question
    assert "Could not look that up" in capsys.readouterr().out


def test_the_page_search_never_starts_fzf(monkeypatch):
    from bdnex.api.webprompter import WebPrompter

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/fzf")
    prompter = WebPrompter(lambda question, image=None: {"choice": None})
    prompter.source = type("S", (), {"catalogue": lambda self: []})()
    called = []
    prompter._search_by_prompt = lambda start: called.append(start)
    monkeypatch.setattr("bdnex.ui.prompts.fzf_pick", lambda *a: called.append("fzf"))
    prompter.search("/x/Thorgal T3.cbz")
    assert len(called) == 1 and called[0] != "fzf"
