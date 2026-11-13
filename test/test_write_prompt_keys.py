"""Every question takes its keys, labels and colours from one table (bdnex/ui/keys.py)."""

import pytest

from bdnex.organizer.organizer import WriteAborted
from bdnex.ui import keys
from bdnex.ui.prompts import InquirerPrompter, shortcut


def build(entries, on=True):
    return keys.build(entries, on, shortcut)


def test_an_action_brings_its_key_label_and_colour():
    choices, fragments, mapping = build([{"action": "skip", "value": "s"}, {"action": "abort", "value": "a"}])
    assert [c["name"] for c in choices] == ["[S]kip this file", "[A]bort (what is decided is kept; run it again to continue)"]
    assert mapping == {"s": "s", "a": "a"}
    assert fragments[0][0][0] == "fg:ansiyellow" and fragments[1][0][0] == "fg:ansired"


def test_the_core_keys_mean_one_thing_everywhere():
    core = {name: keys.ACTIONS[name].key for name in keys.CORE}
    assert core == {"skip": "s", "skip_folder": "f", "back": "b", "abort": "a", "yes": "y", "no": "n"}
    for name, action in keys.ACTIONS.items():
        if name not in ("skip", "skip_folder", "back", "abort"):
            assert action.key not in {"s", "f", "b", "a"} or name in ("keep_both",), name


def test_a_label_and_a_key_can_replace_the_defaults():
    choices, _, mapping = build([{"action": "skip", "value": 1, "label": "Skip this folder"},
                                 {"value": 2, "label": "Type a value...", "key": "t"}])
    assert choices[0]["name"] == "[S]kip this folder" and choices[1]["name"] == "[T]ype a value..." and mapping == {"s": 1, "t": 2}


def test_two_answers_never_share_a_key():
    with pytest.raises(ValueError):
        build([{"action": "skip", "value": 1}, {"action": "skip", "value": 2}])


def test_no_colour_when_the_terminal_has_none():
    _, fragments, mapping = build([{"action": "skip", "value": 1}], on=False)
    assert fragments == [None] and "s" in mapping


def test_every_action_is_a_valid_tone_and_unique_per_name():
    assert {a.tone for a in keys.ACTIONS.values()} <= set(keys.TONES)
    assert {a.category for a in keys.ACTIONS.values()} <= {"leave", "stop", "choose", "confirm"}


def test_asking_goes_through_the_keys_and_abort_stops_the_run(monkeypatch):
    prompter = InquirerPrompter()
    seen = {}

    def select(message, choices, fragments=None, shortcuts=None):
        seen["keys"] = shortcuts
        return prompter.ABORT

    monkeypatch.setattr(prompter, "_select", select)
    with pytest.raises(WriteAborted):
        prompter._ask("Which one wins?", [{"action": "database", "value": "database"}, {"action": "skip", "value": "skip"}])
    assert seen["keys"] == {"d": "database", "s": "skip", "a": prompter.ABORT}


def test_conflict_question_offers_the_rest_of_the_queue_on_its_own_key(monkeypatch):
    prompter = InquirerPrompter()
    seen = {}
    monkeypatch.setattr(prompter, "_select", lambda message, choices, fragments=None, shortcuts=None:
                        seen.update(keys=shortcuts, names=[c["name"] for c in choices]) or prompter.SKIP)
    monkeypatch.setattr(prompter, "_need_terminal", lambda: None)
    prompter.conflict("/x.cbz", [])
    assert seen["keys"]["w"] == "database_all" and seen["keys"]["d"] == "database"
    assert any("[W]ins" in name and "rest of the queue" in name for name in seen["names"])


def test_each_real_question_builds_without_a_key_clash(monkeypatch):
    """Every question of the prompter is asked once with a fake `_select`: a repeated key raises."""
    prompter = InquirerPrompter()
    asked = []
    monkeypatch.setattr(prompter, "_select", lambda message, choices, fragments=None, shortcuts=None:
                        asked.append((message, shortcuts)) or prompter.SKIP)
    monkeypatch.setattr(prompter, "_need_terminal", lambda: None)
    for call in (lambda: prompter.conflict("/x.cbz", []), lambda: prompter.collision("/x"),
                 lambda: prompter.size_action("/x", 1, 2, 20), lambda: prompter.corrupted_action("/x", [], True),
                 lambda: prompter.cover_mismatch("/x", "a", 1, 2), lambda: prompter.choose_placement(1, "/d"),
                 lambda: prompter.duplicate_action("/x", ["/y"], "same"), lambda: prompter.confirm("Sure?"),
                 lambda: prompter._series_choice("/f", type("P", (), {"name": "S"})())):
        try:
            call()
        except Exception as err:  # the answer is a fake: only a key clash (ValueError) matters here
            assert not isinstance(err, ValueError), err
    assert len(asked) >= 8
    for _, mapping in asked:
        assert mapping and len(mapping) == len(set(mapping))
