import io
import re

import pytest

from bdnex.organizer import comicinfo
from bdnex.ui import color, diffview

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip(text):
    return ANSI.sub("", text)


def changes():
    return comicinfo.diff_fields(
        {"Writer": "Wilfrid Lupano", "Format": "One Shot", "Series": "Same"},
        {"Writer": "Lupano, Wilfrid", "Year": "2021", "Series": "Same"})  # Format: only in the file, kept


def test_plain_layout_is_a_familiar_minus_plus_diff():
    lines = diffview.render(changes(), on=False)
    assert lines == ["  ~ Writer",
                     "      - Wilfrid Lupano",
                     "      + Lupano, Wilfrid",
                     "  + Year    2021"]
    assert not any("Format" in line for line in lines)  # a file-only field is not shown as a change
    assert not any("\x1b" in line for line in lines)


def test_colours_follow_the_convention():
    lines = diffview.render(changes(), on=True)
    assert lines[0].startswith("\x1b[33m")  # changed: yellow header
    assert "\x1b[31m      - \x1b[0m" in lines[1] and "\x1b[32m      + \x1b[0m" in lines[2]
    assert lines[3].startswith("\x1b[32m") and "2021" in lines[3]  # only in the database: green
    assert [strip(x) for x in lines] == diffview.render(changes(), on=False)  # colour adds no text


def test_only_the_changed_words_are_emphasised():
    old, new = diffview.highlight("Série Nains tome un", "Série Nains tome deux", on=True)
    assert old == "Série Nains tome \x1b[1;31mun\x1b[0m" and new == "Série Nains tome \x1b[1;32mdeux\x1b[0m"
    old, new = diffview.highlight("same", "same", on=True)
    assert old == new == "same"


def test_without_colour_highlight_is_plain():
    assert diffview.highlight("a b", "a c", on=False) == ("a b", "a c")


def test_field_names_are_aligned():
    lines = diffview.render(comicinfo.diff_fields({}, {"Day": "1", "CommunityRating": "4.20"}), on=False)
    assert lines == ["  + CommunityRating  4.20", "  + Day              1"]


def test_long_values_carry_a_dim_note():
    [change] = comicinfo.diff_fields({"Summary": "x " * 300}, {"Summary": "x " * 100})
    lines = diffview.render([change], on=True)
    assert "\x1b[2m" in lines[0] and "characters" in strip(lines[0])


def test_conflict_text_has_header_legend_and_changes():
    text = strip(diffview.conflict_text("book.cbz", changes(), on=True))
    assert text.splitlines()[:3] == ["book.cbz", "ComicInfo.xml in the file differs from the database:",
                                     "red = in the file   green = in the database   (only changed words are bold; other fields of the file are kept)"]
    assert "  ~ Writer" in text


# ---------------------------------------------------------------------- colour on/off
class Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("NO_COLOR", "FORCE_COLOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def test_colour_only_on_a_terminal(clean_env):
    assert color.enabled(Stream(True)) is True
    assert color.enabled(Stream(False)) is False  # a pipe or a file


def test_no_color_and_dumb_terminals_win(clean_env, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert color.enabled(Stream(True)) is False
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert color.enabled(Stream(True)) is False


def test_force_color(clean_env, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert color.enabled(Stream(False)) is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert color.enabled(Stream(False)) is False  # NO_COLOR is the stronger request


def test_paint():
    assert color.paint("x", "red", True) == "\x1b[31mx\x1b[0m"
    assert color.paint("x", "red", False) == "x" and color.paint("", "red", True) == ""


# ------------------------------------------------------------------ the real prompt
def test_the_conflict_prompt_prints_the_readable_diff(monkeypatch, capsys, clean_env):
    from bdnex.ui.prompts import InquirerPrompter

    monkeypatch.setattr(InquirerPrompter, "_select", lambda self, *args, **kw: "database")
    assert InquirerPrompter().conflict("/x/book.cbz", changes()) == "database"
    out = capsys.readouterr().out
    assert "/x/book.cbz" in out and "  ~ Writer" in out and "      + Lupano, Wilfrid" in out
    assert "\x1b" not in out  # captured output is not a terminal
    monkeypatch.setenv("FORCE_COLOR", "1")
    InquirerPrompter().conflict("/x/book.cbz", changes())
    assert "\x1b[32m" in capsys.readouterr().out
