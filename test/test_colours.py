"""The match question in colour: graded scores, folder and file name apart, a rule between albums."""

import io
import re
import types

import pytest
from InquirerPy import inquirer

from bdnex.importer.matching import Match
from bdnex.importer.source import Candidate
from bdnex.ui import color, results
from bdnex.ui.prompts import InquirerPrompter, colorize_choices, match_header
from test_outside import world  # noqa: F401  (a library folder and a download folder)

ANSI = re.compile(r"\x1b\[[0-9;]*m")


# --------------------------------------------------------------------- the gradient
@pytest.mark.parametrize("score, name", [(0, "red"), (20, "red"), (30, "red-orange"), (40, "orange"), (60, "yellow"),
                                         (80, "green"), (100, "green")])
def test_score_colours_run_from_red_to_green(score, name):
    red, green, blue = color.score_rgb(score)
    assert {"red": red > 180 and green < 80, "red-orange": red > 200 and 60 < green < 110,
            "orange": red > 200 and 80 < green < 120, "yellow": red > 200 and green > 150,
            "green": green > 110 and red < 80}[name], (score, (red, green, blue))


def test_the_gradient_is_continuous_and_clamped():
    assert color.score_rgb(-50) == color.score_rgb(20) and color.score_rgb(150) == color.score_rgb(100)
    steps = [color.score_rgb(s) for s in range(20, 101)]
    assert all(max(abs(a - b) for a, b in zip(x, y)) < 20 for x, y in zip(steps, steps[1:]))  # no jumps


def test_a_strong_match_is_greener_than_a_weak_one():
    assert color.score_rgb(95)[1] > color.score_rgb(95)[0] and color.score_rgb(25)[0] > color.score_rgb(25)[1]


def test_painting_a_score(monkeypatch):
    monkeypatch.delenv("COLORTERM", raising=False)
    assert color.paint_score(" 85.0", 85, False) == " 85.0"  # colour off: untouched
    assert re.fullmatch(r"\x1b\[38;5;\d+m 85\.0\x1b\[0m", color.paint_score(" 85.0", 85, True))
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert re.fullmatch(r"\x1b\[38;2;\d+;\d+;\d+m 85\.0\x1b\[0m", color.paint_score(" 85.0", 85, True))


def test_a_rule_is_as_wide_as_the_terminal_within_limits():
    assert len(color.rule(60)) == 60 and len(color.rule(500)) == 100 and len(color.rule(3)) == 20


# ------------------------------------------------------------------- the header
PATH = "/home/me/comics/OneShot/100 Maisons - La Cité Des Abeilles (Delphine Le Lay) [2015]/100 Maisons - La Cité Des Abeilles.cbz"


def test_the_header_shows_the_folder_and_the_file_not_the_whole_path():
    plain = match_header(PATH, False)
    lines = plain.strip("\n").splitlines()
    assert lines[0].startswith("─") and lines[1] == "100 Maisons - La Cité Des Abeilles (Delphine Le Lay) [2015]/100 Maisons - La Cité Des Abeilles.cbz"
    assert "/home/me" not in plain and "OneShot" not in plain


def test_folder_and_file_name_are_painted_apart_and_the_rule_is_dim():
    painted = match_header(PATH, True).strip("\n").splitlines()
    assert painted[0].startswith("\x1b[2m")  # the rule, dim
    folder, name = painted[1].split("/100 Maisons - La Cité Des Abeilles.cbz")[0], "100 Maisons - La Cité Des Abeilles.cbz"
    assert "\x1b[36m" in painted[1] and "\x1b[1m" + name in painted[1]  # the folder cyan, the file bold
    assert ANSI.sub("", painted[1]) == "100 Maisons - La Cité Des Abeilles (Delphine Le Lay) [2015]/" + name


def test_a_file_without_a_folder_is_just_its_name():
    assert match_header("a.cbz", False).strip("\n").splitlines()[1] == "a.cbz"


# ------------------------------------------------------------------- the choices
def candidate(ident, slug):
    return Candidate(str(ident), f"https://www.bedetheque.com/{slug}-{ident}.html", slug.lower())


A, B = candidate(1, "BD-Bibliomule-de-Cordoue"), candidate(2, "BD-Cite-des-ombres")


def real_prompt(fragments):
    choices = [{"name": "plain a", "value": A}, {"name": "plain b", "value": B}, {"name": "plain skip", "value": "skip"}]
    prompt = inquirer.select(message="m", choices=choices)
    colorize_choices(prompt, fragments)
    return prompt.content_control


def test_rows_are_drawn_in_pieces_with_the_pointer_and_marker_kept():
    control = real_prompt([[("fg:#2ea043 bold", " 85.0"), ("", "  BD-A.html")], None, [("fg:ansiyellow", "Skip")]])
    hover = control._get_hover_text(control.choices[0])
    assert hover[0] == ("class:pointer", "❯") and hover[-2:] == [("fg:#2ea043 bold", " 85.0"), ("bold", "  BD-A.html")]
    normal = control._get_normal_text(control.choices[0])
    assert normal[-2:] == [("fg:#2ea043 bold", " 85.0"), ("", "  BD-A.html")] and normal[0] == ("", " ")
    assert control._get_normal_text(control.choices[1])[-1] == ("", "plain b")  # no pieces: as InquirerPy draws it
    assert control._get_normal_text(control.choices[2])[-1] == ("fg:ansiyellow", "Skip")


def test_the_whole_list_still_renders_with_a_line_per_choice():
    control = real_prompt([[("fg:ansired", "x")], None, None])
    text = "".join(t for _, t in control._get_formatted_choices())
    assert text.count("\n") == 2 and "plain b" in text


class Seen(InquirerPrompter):
    def _need_terminal(self):
        pass

    def _select(self, message, choices, fragments=None, shortcuts=None):
        self.message, self.choices, self.fragments, self.shortcuts = message, choices, fragments, shortcuts
        return InquirerPrompter.SKIP


def ask(monkeypatch, on, source=object()):
    monkeypatch.setattr(color, "enabled", lambda stream=None: on)
    prompter = Seen(source)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    prompter.choose_match(PATH, [Match(A, 91.0, 91.0), Match(B, 28.0, 28.0)])
    return prompter, out.getvalue()


def test_each_score_has_its_own_colour_and_the_other_entries_theirs(monkeypatch):
    prompter, _ = ask(monkeypatch, on=True)
    score_style = lambda pieces: next(style for style, _ in pieces if style.startswith("fg:#"))  # noqa: E731
    styles = [score_style(pieces) for pieces in prompter.fragments[:2]]
    assert styles == [f"fg:{color.score_hex(91.0)} bold", f"fg:{color.score_hex(28.0)} bold"] and styles[0] != styles[1]
    # search, skip, back, abort: cyan, yellow, blue, red
    assert [p[0][0] for p in prompter.fragments[2:]] == ["fg:ansicyan", "fg:ansiyellow", "fg:ansiyellow", "fg:ansiblue", "fg:ansired"]
    assert prompter.choices[0]["name"] == "[1]  91.0  BD-Bibliomule-de-Cordoue-1.html"


def test_without_colour_the_plain_names_are_drawn(monkeypatch):
    prompter, output = ask(monkeypatch, on=False)
    assert all(pieces is None for pieces in prompter.fragments)
    assert "\x1b" not in output


def test_the_question_is_short_because_the_header_names_the_file(monkeypatch):
    prompter, output = ask(monkeypatch, on=False)
    assert prompter.message == "Which album?" and PATH not in output
    assert "100 Maisons - La Cité Des Abeilles.cbz" in output and output.lstrip("\n").startswith("─")


def test_albums_are_separated_by_a_rule(monkeypatch):
    first, output = ask(monkeypatch, on=False)
    monkeypatch.setattr("sys.stdout", io.StringIO())
    second_out = io.StringIO()
    monkeypatch.setattr("sys.stdout", second_out)
    Seen(object()).choose_match(PATH, [Match(A, 80.0, 80.0)])
    assert output.count("─") >= 20 and second_out.getvalue().count("─") >= 20  # one rule before every question


# ----------------------------------------------------------------- the report
def test_the_import_report_is_in_colour_only_when_asked_or_on_a_terminal(world, monkeypatch):
    from test_outside import do

    root, downloads, comic, _ = world
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    plain = do("--match", "--auto", "-n", str(downloads))[1]  # a StringIO is not a terminal
    assert "\x1b" not in plain and "pretend" in plain
    monkeypatch.setenv("FORCE_COLOR", "1")
    painted = do("--match", "--auto", "-n", str(downloads))[1]
    assert "\x1b[1;36mpretend  " in painted  # the outcome in its colour (cyan for a preview, as in write)
    assert re.search(r"\x1b\[38;[25];[\d;]+m100\.0\x1b\[0m", painted)  # a perfect score, on the gradient
    assert ANSI.sub("", painted) == plain  # only colour was added
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b" not in do("--match", "--auto", "-n", str(downloads))[1]


def test_outcomes_have_a_colour_each():
    from bdnex.ui import import_cmd

    assert results.STYLES.keys() >= {"tagged", "partial", "skipped", "failed", "already", "duplicate", "pretend"}


def test_progress_lines_are_tinted_only_on_a_terminal():
    from bdnex.ui import output
    plain = "    match: BD-Foo-Tome-1-9.html (series \"Foo\")"
    assert output._tint(plain, False) == plain
    assert "\x1b[32m" in output._tint(plain, True)  # a match is green
    counted = output._tint("[3/10] Foo T03.cbz", True)
    assert "\x1b[36m[3/10]" in counted and "\x1b[1mFoo T03.cbz" in counted


def test_a_banner_names_the_step(capsys):
    from bdnex.ui import output
    output.banner("Matching 5 file(s) to albums", 1, 2)
    err = capsys.readouterr().err
    assert "Step 1/2  Matching 5 file(s) to albums" in err and "─" in err
    output.banner("Result")
    assert "Step" not in capsys.readouterr().err


def test_a_progress_bar_can_be_redrawn_with_new_text_without_counting_a_step():
    import io
    from bdnex.ui.output import Bar
    stream = io.StringIO()
    bar = Bar(7, "Album pages", stream=stream, tty=True)
    bar.update("BD-One.html")
    bar.refresh("BD-One.html  (3 being fetched, 4 of 7 fetched)")
    assert bar.done == 1 and "3 being fetched" in stream.getvalue()
    quiet = io.StringIO()
    Bar(7, "x", stream=quiet, tty=False).refresh("nothing on a pipe")
    assert quiet.getvalue() == ""


def test_a_progress_line_is_cut_to_the_width_of_the_terminal(monkeypatch):
    import io
    import os
    from bdnex.ui.output import Bar
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=None: os.terminal_size((60, 20)))
    stream = io.StringIO()
    bar = Bar(7, "Album pages", stream=stream, tty=True)
    bar.update("Abaddon-Shadmi-Tome-1-Abaddon · Ada-Baldi-Ada · Adieu-Bridavoine-Tardi  (3 of 6 fetched)")
    drawn = stream.getvalue().replace("\r\x1b[K", "")
    assert len(drawn) <= 59 and drawn.endswith("…")
    stream.truncate(0), stream.seek(0)
    bar.update("short")
    assert "short" in stream.getvalue() and "…" not in stream.getvalue()


def test_one_table_gives_the_colour_of_every_outcome_for_every_command():
    from bdnex.ui import results

    # the words the commands print for results all resolve in the one table
    for outcome in ("written", "moved", "copied", "repaired", "unchanged", "skipped", "failed", "pretend", "tagged",
                    "partial", "already", "duplicate", "updated", "found", "lost", "ambiguous"):
        assert outcome in results.STYLES, outcome
    assert results.style("something new") == "dim"
    block = results.block("failed", "a.cbz", ["why"], True)
    assert "\x1b[1;31mfailed" in block and "\x1b[1ma.cbz" in block and block.endswith("    why")
    assert results.block("", "Title", [], False, "problem") == "\n● Title"
