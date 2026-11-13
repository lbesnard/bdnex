"""Searching the album list by hand from the match prompt: fzf, the Python fallback, and Ctrl-O."""

import shutil
import subprocess
import sys
import types

import pytest
from InquirerPy import inquirer
from prompt_toolkit.keys import Keys

from bdnex.importer.bedetheque import Bedetheque
from bdnex.importer.matching import Match
from bdnex.importer.session import NeedsTerminal
from bdnex.importer.source import Candidate
from bdnex.library import Library
from bdnex.ui import prompts
from bdnex.ui.prompts import FUZZY_POOL, InquirerPrompter, bind_open, display_name, open_page

BASE = "https://www.bedetheque.com/"
ABEILLES = Candidate("236603", BASE + "BD-100-maisons-La-cite-des-abeilles-236603.html", "100 maisons cite abeilles")
OMBRES = Candidate("409011", BASE + "BD-Espions-Tome-6-La-Cite-des-ombres-409011.html", "espions 6 cite ombres", 6)
SABLES = Candidate("540372", BASE + "BD-Cite-des-sables-Tome-1-540372.html", "cite sables 1", 1)
CATALOGUE = [ABEILLES, OMBRES, SABLES]
FILE = "/comics/100 Maisons - La Cité Des Abeilles (Delphine Le Lay, Marion Boé) [2015].cbz"


class Source:
    def __init__(self, catalogue=True):
        self.asked = []
        if catalogue:
            self.catalogue = lambda: CATALOGUE

    def candidates(self, query, limit=5):
        self.asked.append((query, limit))
        return [(c, 80.0) for c in CATALOGUE if any(w in c.name for w in query.lower().split())][:limit]


class Scripted(InquirerPrompter):
    """The real prompter with its two InquirerPy calls replaced by scripted answers."""

    def __init__(self, source=None, selects=(), texts=(), fuzzies=()):
        super().__init__(source)
        self.selects, self.texts, self.fuzzies = list(selects), list(texts), list(fuzzies)
        self.shown = []

    def _need_terminal(self):
        pass

    def _select(self, message, choices, fragments=None, shortcuts=None):
        self.fragments, self.shortcuts = fragments, shortcuts
        self.shown.append((message, choices))
        answer = self.selects.pop(0)
        return answer(choices) if callable(answer) else answer

    def _text(self, message, default=""):
        self.shown.append((message, default))
        return self.texts.pop(0)

    def _fuzzy(self, message, choices):
        self.shown.append((message, choices))
        return self.fuzzies.pop(0)


def matches():
    return [Match(ABEILLES, 85.0, 85.0), Match(OMBRES, 30.0, 30.0)]


# -------------------------------------------------------------------- display names
@pytest.mark.parametrize("candidate, expected", [
    (ABEILLES, "100 maisons La cite des abeilles"),
    (OMBRES, "Espions Tome 6 La Cite des ombres"),
    (Candidate("1", "https://other.example/album/1", "some normalised name"), "some normalised name"),
])
def test_display_name(candidate, expected):
    assert display_name(candidate) == expected


def test_the_source_lists_its_albums_for_searching(tmp_path):
    source = Bedetheque(Library(tmp_path / "l.sqlite"))
    with pytest.raises(FileNotFoundError):  # no album list yet
        source.catalogue()
    source._index = CATALOGUE
    assert source.catalogue() == CATALOGUE


# ---------------------------------------------------------------------- the choice
def test_the_search_choice_is_offered_only_when_there_is_something_to_search():
    plain = Scripted(None, selects=[lambda ch: ch[0]["value"]])
    assert plain.choose_match(FILE, matches()) == ABEILLES
    assert [c["value"] for c in plain.shown[0][1]] == [ABEILLES, OMBRES, InquirerPrompter.SKIP, InquirerPrompter.SKIP_FOLDER, InquirerPrompter.PREVIOUS, InquirerPrompter.ABORT]
    with_source = Scripted(Source(), selects=[InquirerPrompter.SKIP])
    with_source.choose_match(FILE, matches())
    labels = [c["name"] for c in with_source.shown[0][1]]
    assert labels[-5:-1] == ["[E]nter a search in the album list...", "[S]kip this file", "Skip the whole [F]older (every file of it still to come)",
                            "[B]ack to the previous file"]
    assert labels[-1].startswith("[A]bort the import")


def test_a_manual_pick_is_the_answer():
    prompter = Scripted(Source(), selects=[InquirerPrompter.SEARCH, SABLES])  # the second answer: "yes, use it"
    prompter.search = lambda path: SABLES
    assert prompter.choose_match(FILE, matches()) is SABLES
    assert prompter.shown[1][0].startswith("Use ")  # it was checked before being taken


def test_a_picked_album_can_be_refused_after_checking_it_and_the_list_comes_back():
    prompter = Scripted(Source(), selects=[InquirerPrompter.SEARCH, InquirerPrompter.NO, OMBRES])
    prompter.search = lambda path: SABLES
    assert prompter.choose_match(FILE, matches()) is OMBRES and len(prompter.shown) == 3


def test_an_address_typed_by_hand_is_an_answer():
    class WithUrl(Source):
        def candidate_from_url(self, url):
            return SABLES if "BD-" in url else None

    prompter = Scripted(WithUrl(), selects=[InquirerPrompter.URL])
    prompter._text = lambda message, default="": next(texts)
    texts = iter(["nonsense", "https://www.bedetheque.com/BD-Sables-1.html"])
    assert prompter.choose_match(FILE, matches()) is SABLES
    labels = [c["name"] for c in prompter.shown[0][1]]
    assert "Type the album's [U]RL..." in labels


def test_an_empty_address_goes_back_to_the_list():
    class WithUrl(Source):
        def candidate_from_url(self, url):
            return None

    prompter = Scripted(WithUrl(), selects=[InquirerPrompter.URL, OMBRES])
    prompter._text = lambda message, default="": ""
    assert prompter.choose_match(FILE, matches()) is OMBRES


@pytest.mark.parametrize("typed, expected", [
    ("https://www.bedetheque.com/BD-Loup-Noir-Taupinambour-Tome-5-116343.html", ("116343", 5)),
    ("  http://m.bedetheque.com/BD-Drain-Tome-2-Chatiment-79365.html?x=1#top ", ("79365", 2)),
    ("https://www.bedetheque.com/BD-Un-seul-album-12.html", ("12", None)),
    ("https://www.bedetheque.com/serie-1-BD-Loup-Noir.html", None),
    ("https://example.com/BD-X-1.html", None),
    ("", None),
])
def test_a_typed_address_becomes_a_candidate(tmp_path, typed, expected):
    found = Bedetheque(Library(tmp_path / "l.sqlite")).candidate_from_url(typed)
    assert (found and (found.source_id, found.volume)) == (expected or None)
    if found:
        assert found.url.startswith("https://www.bedetheque.com/BD-") and "?" not in found.url


def test_backing_out_of_the_search_returns_to_the_list():
    prompter = Scripted(Source(), selects=[InquirerPrompter.SEARCH, InquirerPrompter.SEARCH, OMBRES])
    results = iter([None, None])
    prompter.search = lambda path: next(results)
    assert prompter.choose_match(FILE, matches()) is OMBRES and len(prompter.shown) == 3  # the list came back each time


def test_skipping_still_works():
    assert Scripted(Source(), selects=[InquirerPrompter.SKIP]).choose_match(FILE, matches()) is None


def test_without_a_terminal_the_prompt_refuses(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    with pytest.raises(NeedsTerminal):
        InquirerPrompter(Source()).choose_match(FILE, matches())


# ------------------------------------------------------------------------------ fzf
def fake_fzf(monkeypatch, stdout="", returncode=0):
    seen = {}
    monkeypatch.setattr(prompts.shutil, "which", lambda name: "/usr/bin/fzf" if name == "fzf" else None)

    def run(command, **kw):
        seen.update(command=command, **kw)
        return subprocess.CompletedProcess(command, returncode, stdout=stdout)

    monkeypatch.setattr(prompts.subprocess, "run", run)
    return seen


def test_fzf_gets_every_album_case_insensitively_with_the_file_name_as_the_first_query(monkeypatch):
    seen = fake_fzf(monkeypatch, stdout="100 maisons La cite des abeilles\t236603\thttps://x\n")
    found = Scripted(Source()).search(FILE)
    command = seen["command"]
    assert found is ABEILLES
    assert command[0] == "fzf" and "-i" in command  # case-insensitive, whatever fzf's smart-case would do
    assert command[command.index("--query") + 1] == "100 maisons la cite des abeilles"  # parentheses dropped, folded
    assert command[command.index("--with-nth") + 1] == "1"  # only the title is shown, the id and address ride along
    lines = seen["input"].split("\n")
    assert len(lines) == 3 and lines[0] == f"100 maisons La cite des abeilles\t236603\t{ABEILLES.url}"


def test_ctrl_o_in_fzf_opens_the_page_address_with_the_browser(monkeypatch):
    seen = fake_fzf(monkeypatch, stdout="")
    Scripted(Source()).search(FILE)
    bind = seen["command"][seen["command"].index("--bind") + 1]
    assert bind.startswith("ctrl-o:execute-silent(") and "-m webbrowser {3}" in bind and sys.executable in bind


@pytest.mark.parametrize("returncode, stdout", [(1, ""), (130, ""), (2, "x\t1\t2\n"), (0, ""), (0, "gone\t999999\turl\n")])
def test_nothing_picked_in_fzf_means_back(monkeypatch, returncode, stdout):
    fake_fzf(monkeypatch, stdout=stdout, returncode=returncode)
    assert Scripted(Source()).search(FILE) is None


@pytest.mark.skipif(not shutil.which("fzf"), reason="fzf is not installed")
def test_the_real_fzf_matches_without_regard_to_case_and_returns_the_whole_line():
    lines = "\n".join(f"{display_name(c)}\t{c.source_id}\t{c.url}" for c in CATALOGUE)
    for query in ("CITE ABEILLES", "cite abeilles", "Cite Abeilles"):
        out = subprocess.run(["fzf", "-i", "--filter", query, "--delimiter", "\t", "--with-nth", "1"],
                             input=lines, capture_output=True, text=True).stdout
        assert out.strip().split("\t")[1] == "236603", query


# ------------------------------------------------------------ without fzf: Python fuzzy
def without_fzf(monkeypatch):
    monkeypatch.setattr(prompts.shutil, "which", lambda name: None)


def test_without_fzf_you_name_words_then_filter_the_best_hits(monkeypatch):
    without_fzf(monkeypatch)
    source = Source()
    prompter = Scripted(source, texts=["cite abeilles"], fuzzies=[ABEILLES])
    assert prompter.search(FILE) is ABEILLES
    assert prompter.shown[0][1] == "100 maisons la cite des abeilles"  # the prompt starts from the file name
    assert source.asked == [("cite abeilles", FUZZY_POOL)]
    message, choices = prompter.shown[1]
    assert [c["name"] for c in choices][0] == "100 maisons La cite des abeilles" and "for 'cite abeilles'" in message


def test_without_fzf_an_empty_answer_goes_back(monkeypatch):
    without_fzf(monkeypatch)
    assert Scripted(Source(), texts=["   "]).search(FILE) is None


def test_ctrl_c_in_the_filter_returns_to_the_words_and_nothing_found_asks_again(monkeypatch, capsys):
    without_fzf(monkeypatch)
    prompter = Scripted(Source(), texts=["zzzz", "cite", ""], fuzzies=[None])
    assert prompter.search(FILE) is None
    assert "Nothing in the album list looks like 'zzzz'" in capsys.readouterr().out
    assert [text for kind, text in prompter.shown if isinstance(text, str) and kind.startswith("Words")] == \
        ["100 maisons la cite des abeilles", "zzzz", "cite"]  # each prompt starts from the previous words


def test_a_source_without_a_catalogue_uses_the_python_prompt_even_with_fzf(monkeypatch):
    seen = fake_fzf(monkeypatch)
    prompter = Scripted(Source(catalogue=False), texts=["cite"], fuzzies=[SABLES])
    assert prompter.search(FILE) is SABLES and "command" not in seen


# ------------------------------------------------------------------------- Ctrl-O
def test_ctrl_o_opens_the_highlighted_candidates_page_in_the_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(prompts.webbrowser, "open", lambda url: opened.append(url) or True)
    assert open_page(ABEILLES) is True and opened == [ABEILLES.url]
    assert open_page(InquirerPrompter.SKIP) is False and open_page(None) is False and opened == [ABEILLES.url]


def test_ctrl_o_is_bound_in_both_kinds_of_prompt(monkeypatch):
    choices = [{"name": "a", "value": ABEILLES}, {"name": "skip", "value": InquirerPrompter.SKIP}]
    for prompt in (inquirer.select(message="m", choices=choices),
                   inquirer.fuzzy(message="m", choices=choices, raise_keyboard_interrupt=False, mandatory=False)):
        bind_open(prompt)
        bound = {tuple(b.keys): b.handler for b in prompt._kb.bindings}
        assert (Keys.ControlO,) in bound
        opened = []
        monkeypatch.setattr(prompts.webbrowser, "open", lambda url: opened.append(url) or True)
        bound[(Keys.ControlO,)](types.SimpleNamespace())  # what pressing the key does
        assert opened == [ABEILLES.url]  # the first (highlighted) choice

