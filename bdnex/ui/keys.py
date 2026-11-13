"""The keys of every question, in one table: the same action has the same key, label and colour everywhere.

``skip`` is ``S`` in the match question, the series question, the review and the write questions;
``abort`` is always ``A`` and red. A question lists the actions it offers (:func:`build`), plus one-off
answers of its own, and gets back its choices, their coloured pieces and the ``{key: value}`` map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TONES = {"green": "fg:ansigreen", "red": "fg:ansired", "yellow": "fg:ansiyellow", "blue": "fg:ansiblue", "cyan": "fg:ansicyan"}
RESERVED = {"o"}  # `o` opens an album's page in the match questions


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    tone: str  # a name of TONES
    category: str  # leave: put it off | stop: end the run | choose: pick an answer | confirm: yes / no


# green = safe, yellow = put off / decline, red = destructive or stop, cyan = a choice, blue = go back
ACTIONS: dict[str, Action] = {
    "skip": Action("s", "Skip this file", "yellow", "leave"),
    "skip_folder": Action("f", "Skip the whole folder (every file of it still to come)", "yellow", "leave"),
    "back": Action("b", "Back to the previous file", "blue", "leave"),
    "abort": Action("a", "Abort (what is decided is kept; run it again to continue)", "red", "stop"),
    "yes": Action("y", "Yes", "green", "confirm"),
    "no": Action("n", "No", "yellow", "confirm"),
    "keep": Action("k", "Keep the match (it is right)", "green", "choose"),
    "unlink": Action("l", "Unlink the file from this album", "yellow", "choose"),
    "rematch": Action("r", "Rematch: choose the album again", "cyan", "choose"),
    "choose": Action("c", "Choose which ones", "cyan", "choose"),
    "search": Action("e", "Enter a search in the album list...", "cyan", "choose"),
    "url": Action("u", "Type the album's URL...", "cyan", "choose"),
    "database": Action("d", "Database wins (overwrite the file's values)", "green", "choose"),
    "database_all": Action("w", "Database wins for the rest of the queue (no more questions)", "green", "choose"),
    "file": Action("k", "Keep the file as it is (the library takes its values)", "red", "choose"),
    "replace": Action("r", "Replace", "red", "choose"),
    "keep_existing": Action("e", "Keep the existing file", "cyan", "choose"),
    "keep_new": Action("n", "Keep the new file", "cyan", "choose"),
    "keep_both": Action("b", "Keep both", "cyan", "choose"),
    "refuse": Action("r", "Refuse it: keep the original untouched", "yellow", "choose"),
    "accept": Action("n", "Accept the new archive", "green", "choose"),
    "leave": Action("l", "Leave it as it is", "yellow", "leave"),
    "move": Action("m", "Move it", "cyan", "choose"),
    "copy": Action("c", "Copy it", "cyan", "choose"),
}
CORE = ("skip", "skip_folder", "back", "abort", "yes", "no")  # these keys never change meaning


def bracket(label: str, key: str) -> str:
    """``Skip this file`` + ``s`` -> ``[S]kip this file`` (at the start of a word when the letter is there)."""
    starts = [m.start() for m in re.finditer(rf"(?i)\b{re.escape(key)}", label)]
    spot = starts[0] if starts else label.lower().find(key)
    if spot < 0:
        return f"[{key.upper()}] {label}"
    return f"{label[:spot]}[{label[spot].upper()}]{label[spot + 1:]}"


def _pieces(name: str, tone: str, shortcut) -> list:
    found = re.search(r"\[(\w)\]", name)
    return [(tone, name[:found.start()])] + shortcut(found.group(1), name[found.end():], tone)


def build(entries: list, on: bool, shortcut):
    """``(choices, fragments, keys)`` for a question.

    Each entry is a dict: ``value`` (what the answer returns) and either an ``action`` of :data:`ACTIONS` (its key,
    colour and label, any of which ``label`` / ``key`` / ``tone`` replace) or a ``label`` of its own (then ``key`` is
    given, else the first letter not used yet). Two entries of one question never share a key.
    """
    taken = set(RESERVED)
    keyed = []
    for entry in entries:
        action = ACTIONS.get(entry.get("action", ""))
        label = entry.get("label") or (action.label if action else "")
        key = (entry.get("key") or (action.key if action else "")).lower()
        found = re.search(r"\[(\w)\]", label)
        if found:
            key = found.group(1).lower()
        if key:
            if key in taken:
                raise ValueError(f"the key {key!r} is used twice in one question ({label!r})")
            taken.add(key)
        keyed.append((entry, action, label, key))
    choices, fragments, keys = [], [], {}
    for entry, action, label, key in keyed:
        if not key:  # no key given: the first letter of the label nobody uses
            key = next((c for c in label.lower() if c.isalpha() and c not in taken), "")
            taken.add(key)
        name = label if re.search(r"\[(\w)\]", label) or not key else bracket(label, key)
        choices.append({"name": name, "value": entry["value"]})
        tone = TONES[entry.get("tone") or (action.tone if action else "cyan")]
        fragments.append(_pieces(name, tone, shortcut) if on and key else None)
        if key:
            keys[key] = entry["value"]
    return choices, fragments, keys
