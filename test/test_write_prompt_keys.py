"""The questions of write / move have keys and colours, like the match question."""

import pytest

from bdnex.organizer.organizer import WriteAborted
from bdnex.ui.prompts import InquirerPrompter, keyed_choices

CHOICES = [{"name": "Database (overwrite)", "value": "database"}, {"name": "File (keep it)", "value": "file"},
           {"name": "Skip this file", "value": "skip"}, {"name": "[A]bort the run", "value": "__abort__"}]


def test_every_answer_gets_a_key_and_none_is_used_twice():
    names, fragments, keys = keyed_choices(CHOICES)
    assert [n["name"] for n in names][:3] == ["[D]atabase (overwrite)", "[F]ile (keep it)", "[S]kip this file"]
    assert keys == {"d": "database", "f": "file", "s": "skip", "a": "__abort__"}
    assert fragments[0][0][0] == "fg:ansigreen" and fragments[1][0][0] == "fg:ansired"  # the diff's colours


def test_no_colour_when_the_terminal_has_none():
    _, fragments, keys = keyed_choices(CHOICES, on=False)
    assert fragments == [None] * 4 and "d" in keys


def test_an_explicit_key_wins_and_o_is_left_to_the_match_question():
    names, _, keys = keyed_choices([{"name": "Keep the existing file", "value": "skip", "key": "e"},
                                    {"name": "Keep the old one", "value": "x"}])
    assert names[0]["name"] == "Keep th[e] existing file" or "[e]" in names[0]["name"].lower()
    assert "o" not in keys


def test_asking_goes_through_the_keys_and_abort_stops_the_run(monkeypatch):
    prompter = InquirerPrompter()
    seen = {}

    def select(message, choices, fragments=None, shortcuts=None):
        seen["keys"] = shortcuts
        return prompter.ABORT

    monkeypatch.setattr(prompter, "_select", select)
    with pytest.raises(WriteAborted):
        prompter._ask("Which one wins?", [dict(c) for c in CHOICES[:3]])
    assert seen["keys"]["a"] == prompter.ABORT and seen["keys"]["s"] == "skip"
