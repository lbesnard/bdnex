"""Shortcut keys with highlighted letters, going back, and the thumbnail beside the match question."""

import base64
import io
import types
import zipfile

import pytest
from InquirerPy import inquirer

from bdnex.importer.matching import Match
from bdnex.importer.session import GoBack, ImportAborted
from bdnex.importer.source import Candidate
from bdnex.ui import color, prompts, thumbnail
from bdnex.ui.prompts import InquirerPrompter, bind_open, bind_shortcuts, shortcut

from importer.test_covers import png

A = Candidate("1", "https://www.bedetheque.com/BD-Un-1.html", "un", 1)
B = Candidate("2", "https://www.bedetheque.com/BD-Deux-2.html", "deux", 2)
PATH = "/home/me/comics/Series/Foo/Foo - T01.cbz"


# --------------------------------------------------------------- the highlighted letter
def test_a_shortcut_underlines_and_bolds_the_letter_in_the_colour_of_its_line():
    assert shortcut("S", "kip this file", "fg:ansiyellow") == [
        ("fg:ansiyellow", "["), ("fg:ansiyellow bold underline", "S"), ("fg:ansiyellow", "]kip this file")]
    assert shortcut("1", "", "")[1] == ("bold underline", "1")  # no colour of its own: the letter alone stands out


# ------------------------------------------------------------------- the question
class Seen(InquirerPrompter):
    def __init__(self, *args, answer=None, **kw):
        super().__init__(*args, **kw)
        self.answer, self.seen = answer, {}

    def _need_terminal(self):
        pass

    def _thumbnail(self, path):
        self.seen["thumbnail"] = path

    def _select(self, message, choices, fragments=None, shortcuts=None):
        self.seen.update(message=message, choices=choices, fragments=fragments, shortcuts=shortcuts)
        return self.answer(choices) if callable(self.answer) else self.answer


def ask(monkeypatch, source=object(), answer=None, on=False, matches=None):
    monkeypatch.setattr(color, "enabled", lambda stream=None: on)
    monkeypatch.setattr("sys.stdout", io.StringIO())
    prompter = Seen(source, answer=answer)
    outcome = prompter.choose_match(PATH, matches if matches is not None else [Match(A, 90.0, 90.0), Match(B, 50.0, 50.0)])
    return prompter, outcome


def test_every_line_names_its_key_beets_style(monkeypatch):
    prompter, _ = ask(monkeypatch, answer=InquirerPrompter.SKIP)
    names = [c["name"] for c in prompter.seen["choices"]]
    assert names[:2] == ["[1]  90.0  BD-Un-1.html", "[2]  50.0  BD-Deux-2.html"]
    assert names[2:] == ["[E]nter a search in the album list...", "[S]kip this file", "Skip the whole [F]older (every file of it still to come)",
                         "[B]ack to the previous file",
                         "[A]bort the import (what is decided is kept; run it again to continue)"]


def test_the_keys_answer_with_the_value_of_their_line(monkeypatch):
    prompter, _ = ask(monkeypatch, answer=InquirerPrompter.SKIP)
    assert prompter.seen["shortcuts"] == {"1": A, "2": B, "e": InquirerPrompter.SEARCH, "s": InquirerPrompter.SKIP,
                                          "f": InquirerPrompter.SKIP_FOLDER, "b": InquirerPrompter.PREVIOUS, "a": InquirerPrompter.ABORT}


def test_no_search_key_when_there_is_nothing_to_search(monkeypatch):
    prompter, _ = ask(monkeypatch, source=None, answer=InquirerPrompter.SKIP)
    assert "e" not in prompter.seen["shortcuts"] and not any("Enter a search" in c["name"] for c in prompter.seen["choices"])


def test_at_most_nine_numbered_candidates(monkeypatch):
    many = [Match(Candidate(str(i), f"https://x/BD-X-{i}.html", "x", i), 80.0 - i, 80.0 - i) for i in range(1, 13)]
    prompter, _ = ask(monkeypatch, answer=InquirerPrompter.SKIP, matches=many)
    assert [k for k in prompter.seen["shortcuts"] if k.isdigit()] == [str(i) for i in range(1, 10)]


def test_choosing_a_candidate_returns_it(monkeypatch):
    assert ask(monkeypatch, answer=B)[1] is B


def test_back_raises_goback_and_abort_raises_abort(monkeypatch):
    with pytest.raises(GoBack):
        ask(monkeypatch, answer=InquirerPrompter.PREVIOUS)
    with pytest.raises(ImportAborted):
        ask(monkeypatch, answer=InquirerPrompter.ABORT)


def test_the_header_and_the_thumbnail_come_before_the_question(monkeypatch):
    prompter, _ = ask(monkeypatch, answer=InquirerPrompter.SKIP)
    assert prompter.seen["thumbnail"] == PATH and prompter.seen["message"] == "Which album?"


# ------------------------------------------------- the keys on real InquirerPy prompts
def real(choices):
    prompt = inquirer.select(message="m", choices=choices)
    return prompt


def handler(prompt, key):
    from prompt_toolkit.keys import Keys
    for binding in prompt._kb.bindings:
        if tuple(binding.keys) == ((key,) if len(key) == 1 else (Keys(key),)):
            return binding.handler
    raise AssertionError(f"{key!r} is not bound")


def press(prompt, key):
    exited = []
    event = types.SimpleNamespace(app=types.SimpleNamespace(exit=lambda result=None: exited.append(result)))
    handler(prompt, key)(event)
    return exited


def test_a_shortcut_key_answers_the_prompt_like_enter(monkeypatch):
    prompt = real([{"name": "[1] first", "value": A}, {"name": "[S]kip this file", "value": InquirerPrompter.SKIP}])
    bind_shortcuts(prompt, {"1": A, "s": InquirerPrompter.SKIP})
    assert press(prompt, "s") == [InquirerPrompter.SKIP] and press(prompt, "S") == [InquirerPrompter.SKIP]  # either case
    assert press(prompt, "1") == [A]
    assert prompt.status["answered"] and prompt.status["result"] == "[1] first"  # what the prompt shows once answered


def test_o_opens_the_page_of_the_highlighted_album_and_ctrl_o_too(monkeypatch):
    opened = []
    monkeypatch.setattr(prompts.webbrowser, "open", lambda url: opened.append(url) or True)
    prompt = real([{"name": "a", "value": A}, {"name": "skip", "value": InquirerPrompter.SKIP}])
    bind_open(prompt, ("c-o", "o", "O"))
    for key in ("o", "O", "c-o"):
        assert press(prompt, key) == []  # opening a page does not answer the question
    assert opened == [A.url] * 3


# --------------------------------------------------------------------------- the thumbnail
class Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("env, expected", [
    ({"KITTY_WINDOW_ID": "3", "TERM": "xterm-256color"}, thumbnail.KITTY),
    ({"TERM": "xterm-kitty"}, thumbnail.KITTY),
    ({"TERM_PROGRAM": "ghostty", "TERM": "xterm-256color"}, thumbnail.KITTY),
    ({"TERM_PROGRAM": "iTerm.app", "TERM": "xterm-256color"}, thumbnail.ITERM2),
    ({"TERM_PROGRAM": "WezTerm", "TERM": "xterm-256color"}, thumbnail.ITERM2),
    ({"LC_TERMINAL": "iTerm2", "TERM": "xterm-256color"}, thumbnail.ITERM2),
    ({"TERM": "xterm-256color"}, None),  # an ordinary terminal: no picture
    ({"KITTY_WINDOW_ID": "3", "STY": "123.pts"}, None),  # GNU screen swallows the sequences
    ({"TERM": "screen-256color", "TERM_PROGRAM": "iTerm.app"}, None),
    ({"TERM": "dumb", "KITTY_WINDOW_ID": "3"}, None),
])
def test_the_terminal_is_recognised_conservatively(env, expected):
    assert thumbnail.protocol("auto", env, Tty()) == expected


def test_nothing_is_ever_written_to_something_that_is_not_a_terminal():
    assert thumbnail.protocol("auto", {"KITTY_WINDOW_ID": "3"}, io.StringIO()) is None
    assert thumbnail.protocol("kitty", {}, io.StringIO()) is None


def test_the_setting_and_the_environment_can_force_or_forbid():
    assert thumbnail.protocol("kitty", {"TERM": "xterm-256color"}, Tty()) == thumbnail.KITTY  # over ssh, say
    assert thumbnail.protocol("iterm2", {}, Tty()) == thumbnail.ITERM2
    assert thumbnail.protocol("never", {"KITTY_WINDOW_ID": "3"}, Tty()) is None
    assert thumbnail.protocol("auto", {"BDNEX_THUMBNAILS": "iterm2"}, Tty()) == thumbnail.ITERM2  # the variable wins
    assert thumbnail.protocol("kitty", {"BDNEX_THUMBNAILS": "never"}, Tty()) is None


def test_a_picture_is_scaled_and_keeps_its_shape():
    result = thumbnail.small_png(png(1, size=(300, 450)))
    assert result is not None
    data, width, height = result
    assert data.startswith(b"\x89PNG") and (width, height) == (360, 540)
    assert thumbnail.cells(width, height) == (24, 18)  # two thirds as tall as wide, cells twice as tall as wide


def test_something_that_is_not_an_image_gives_no_picture():
    assert thumbnail.small_png(b"not an image") is None and thumbnail.small_png(b"") is None


def test_the_kitty_sequence_is_chunked_and_silent():
    data = thumbnail.small_png(png(1, size=(300, 450)))[0]
    sequence = thumbnail.escape(data, thumbnail.KITTY, 14, 10)
    pieces = sequence.split("\x1b\\")[:-1]
    assert len(pieces) > 1 and all(p.startswith("\x1b_G") for p in pieces)
    first = pieces[0].split(";", 1)[0]
    assert "a=T" in first and "f=100" in first and "c=14" in first and "r=10" in first and "q=2" in first and "C=1" in first
    assert [p.split(";", 1)[0] for p in pieces[1:]][-1] == "\x1b_Gm=0" and first.endswith("m=1")
    assert all(len(p.split(";", 1)[1]) <= thumbnail.KITTY_CHUNK for p in pieces)
    assert base64.b64decode("".join(p.split(";", 1)[1] for p in pieces)) == data  # nothing lost between the chunks


def test_the_iterm2_sequence():
    data = thumbnail.small_png(png(1, size=(300, 450)))[0]
    sequence = thumbnail.escape(data, thumbnail.ITERM2, 14, 10)
    assert sequence.startswith("\x1b]1337;File=inline=1;width=14;height=10;preserveAspectRatio=1:") and sequence.endswith("\x07")
    assert base64.b64decode(sequence.split(":", 1)[1][:-1]) == data


def test_the_picture_is_placed_on_the_far_right_after_making_room():
    placed = thumbnail.place(b"x", thumbnail.KITTY, 14, 10, 120)
    assert placed.startswith("\n" * 10 + "\x1b[10A\x1b7\x1b[107G\x1b_G") and placed.endswith("\x1b8")  # column 120 - 14 + 1


def test_a_narrow_terminal_gets_no_picture_rather_than_one_on_top_of_the_list():
    out = io.StringIO()
    assert thumbnail.show(png(1), thumbnail.KITTY, out, terminal_columns=80) == 0 and out.getvalue() == ""
    assert thumbnail.show(png(1), thumbnail.KITTY, out, terminal_columns=thumbnail.MIN_TERMINAL_COLUMNS - 1) == 0


def test_a_wide_terminal_gets_it_and_the_rows_are_reported():
    out = io.StringIO()
    rows = thumbnail.show(png(1, size=(300, 450)), thumbnail.ITERM2, out, terminal_columns=140)
    assert rows == 18 and out.getvalue().startswith("\n" * 18) and "1337;File" in out.getvalue()


def test_an_unreadable_cover_draws_nothing():
    out = io.StringIO()
    assert thumbnail.show(b"junk", thumbnail.KITTY, out, terminal_columns=140) == 0 and out.getvalue() == ""


# ---------------------------------------------------------- the prompter's use of it
def cbz(tmp_path, name="Foo - T01.cbz"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("p1.png", png(3, size=(300, 450)))
    return str(path)


def test_the_first_page_of_the_file_is_what_is_shown(tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(prompts.thumbnail, "protocol", lambda setting="auto", *a, **k: thumbnail.KITTY)
    monkeypatch.setattr(prompts.shutil, "get_terminal_size", lambda fallback=(80, 24): types.SimpleNamespace(columns=200))
    monkeypatch.setattr(prompts.thumbnail, "draw", lambda picture, *a, **k: shown.append(picture))
    prompter = InquirerPrompter(None)
    prompter._thumbnail(cbz(tmp_path))
    assert len(shown) == 1 and shown[0].kind == thumbnail.KITTY and shown[0].terminal_columns == 200
    assert prompter._picture is shown[0]


def test_the_setting_is_passed_on_and_no_protocol_means_nothing_is_read(tmp_path, monkeypatch):
    settings = []
    monkeypatch.setattr(prompts.thumbnail, "protocol", lambda setting="auto", *a, **k: settings.append(setting))
    monkeypatch.setattr(prompts.thumbnail, "draw", lambda *a, **k: pytest.fail("a picture was drawn"))
    InquirerPrompter(None, thumbnails="never")._thumbnail(cbz(tmp_path))
    assert settings == ["never"]


def test_a_failing_picture_never_stops_the_question(tmp_path, monkeypatch):
    monkeypatch.setattr(prompts.thumbnail, "protocol", lambda *a, **k: thumbnail.KITTY)

    def broken(*a, **k):
        raise RuntimeError("the terminal said no")

    monkeypatch.setattr(prompts.thumbnail, "draw", broken)
    InquirerPrompter(None)._thumbnail(cbz(tmp_path))  # no exception
    InquirerPrompter(None)._thumbnail(str(tmp_path / "missing.cbz"))  # nor for a file that cannot be read


def test_inside_tmux_it_needs_passthrough_and_a_terminal_that_can_be_told_apart(monkeypatch):
    env = {"TMUX": "/tmp/tmux-1/default,1,0", "TERM": "tmux-256color", "TERM_PROGRAM": "tmux",
           "GHOSTTY_RESOURCES_DIR": "/usr/share/ghostty"}
    monkeypatch.setattr(thumbnail, "passthrough", lambda env=None: False)
    assert thumbnail.protocol("auto", env, Tty()) is None
    monkeypatch.setattr(thumbnail, "passthrough", lambda env=None: True)
    assert thumbnail.protocol("auto", env, Tty()) == thumbnail.KITTY
    assert thumbnail.protocol("auto", {**env, "GHOSTTY_RESOURCES_DIR": ""}, Tty()) is None  # unknown terminal around it


def test_through_tmux_doubles_the_escapes_inside_a_passthrough_envelope():
    assert thumbnail.through_tmux("\x1b_Gx\x1b\\") == "\x1bPtmux;\x1b\x1b_Gx\x1b\x1b\\\x1b\\"


def test_only_the_picture_goes_through_tmux():
    placed = thumbnail.place(b"x", thumbnail.KITTY, 14, 10, 120, tmux=True)
    assert placed.startswith("\n" * 10 + "\x1b[10A\x1b7\x1b[107G\x1bPtmux;") and placed.endswith("\x1b\\\x1b8")


def test_clearing_deletes_every_placement_and_only_for_kitty(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    out = io.StringIO()
    thumbnail.clear(thumbnail.KITTY, out)
    assert out.getvalue() == "\x1b_Ga=d,d=A,q=2\x1b\\"
    other = io.StringIO()
    thumbnail.clear(thumbnail.ITERM2, other)
    assert other.getvalue() == ""


# ------------------------------------------------------------------ after a resize
def picture(columns=200):
    return thumbnail.prepare(png(1, size=(300, 450)), thumbnail.KITTY, columns)


def test_a_narrow_terminal_gives_no_picture_to_prepare():
    assert picture(80) is None and picture(200).rows == 18


def test_redraw_goes_up_to_the_first_line_of_the_question_and_back(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    pic = picture()
    out = thumbnail.redraw(pic, 150, 7)
    assert out.startswith("\x1b7\x1b[7A\x1b[127G\x1b_G") and out.endswith("\x1b\\\x1b8")  # 150 - 24 + 1
    assert "A" not in thumbnail.redraw(pic, 150, 0).split("\x1b_G")[0].replace("\x1b7", "")  # no move at the top


class FakeApp:
    def __init__(self, columns, up):
        self.columns, self.log = columns, []
        self.output = types.SimpleNamespace(get_size=lambda: types.SimpleNamespace(columns=self.columns),
                                            write_raw=self.log.append, flush=lambda: None)
        self.renderer = types.SimpleNamespace(_cursor_pos=types.SimpleNamespace(y=up))
        self.after_render = []

        class Hooks(list):
            def __iadd__(hooks, fn):
                hooks.append(fn)
                return hooks
        self.after_render = Hooks()

    def render(self):
        for hook in self.after_render:
            hook(self)


def test_the_picture_follows_a_resize_and_only_then(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    app = FakeApp(200, up=9)
    pic = picture(200)
    prompts.follow_resize(types.SimpleNamespace(application=app), pic)
    app.render()
    assert app.log == []  # same size: nothing to do
    app.columns = 160
    app.render()
    text = "".join(app.log)
    assert text.startswith("\x1b_Ga=d") and "\x1b[9A\x1b[137G" in text and pic.terminal_columns == 160
    app.log.clear()
    app.render()
    assert app.log == []


def test_too_narrow_after_a_resize_the_picture_is_only_removed(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    app = FakeApp(200, up=9)
    prompts.follow_resize(types.SimpleNamespace(application=app), picture(200))
    app.columns = 90
    app.render()
    assert "".join(app.log) == "\x1b_Ga=d,d=A,q=2\x1b\\"


def test_a_failure_while_following_a_resize_is_silent():
    app = FakeApp(200, up=9)
    prompts.follow_resize(types.SimpleNamespace(application=app), picture(200))
    app.output.get_size = lambda: 1 / 0
    app.render()  # no exception


def test_the_series_question_has_coloured_keys_like_the_match_question(monkeypatch):
    prompter, _ = ask(monkeypatch, answer="series")
    from types import SimpleNamespace
    proposal = SimpleNamespace(name="Achille Talon", covered=2)
    prompter.confirm_series("Folder", ["/x/a.cbz", "/x/b.cbz"], proposal)
    assert prompter.seen["shortcuts"] == {"y": "series", "n": "each", "c": "choose", "s": "skip",
                                          "a": InquirerPrompter.ABORT}
    names = [c["name"] for c in prompter.seen["choices"]]
    assert names[0].startswith("[Y]es") and names[2].startswith("[C]hoose") and names[3].startswith("[S]kip this folder")


def test_two_covers_become_one_picture_side_by_side():
    left, right = png(1, size=(200, 300)), png(2, size=(300, 300))
    both = thumbnail.pair(left, right, height=100, gap=10)
    import pymupdf
    pixmap = pymupdf.Pixmap(both)
    assert pixmap.height == 100 and pixmap.width == 67 + 10 + 100  # each scaled to the same height
    assert thumbnail.pair(left, b"not an image") is None
