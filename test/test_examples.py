"""The examples at the end of every command's --help."""

import re
import shlex

import pytest

from bdnex.ui.commands import COMMANDS
from bdnex.ui.examples import EXAMPLES, text


def test_every_command_has_examples_and_only_real_commands_are_named():
    assert [name for name in COMMANDS if name not in EXAMPLES] == []
    assert [name for name in EXAMPLES if name not in COMMANDS] == []


@pytest.mark.parametrize("command", sorted(EXAMPLES))
def test_each_example_runs_its_own_command_with_options_that_exist(command):
    from bdnex.ui.cli_reference import help_of

    help_text = help_of(command)
    for line, note in EXAMPLES[command]:
        words = shlex.split(line)
        assert words[0] == "bdnex" and note
        if words[1] != command:
            continue  # a companion line, like `bdnex ls --files attr:checked` under `mark`: checked with its own command
        for option in (w for w in words if re.fullmatch(r"--?[A-Za-z][\w-]*(=.*)?", w)):
            flag = option.split("=")[0]
            assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text), f"{line!r}: {flag} is not in `bdnex {command} --help`"


def test_the_examples_follow_the_help_of_the_command(capsys):
    from bdnex import ui

    with pytest.raises(SystemExit):
        ui.main(["review", "--help"])
    out = capsys.readouterr().out
    assert "Examples:" in out and "bdnex review --auto -n" in out
    assert text("nonsense") == ""


def test_the_default_aliases_are_valid_explained_and_use_options_that_exist():
    from bdnex.config import load_config
    from bdnex.ui.cli_reference import help_of
    from bdnex.ui.commands import parse_alias

    aliases = load_config().settings.aliases
    assert len(aliases) >= 8
    for text_ in aliases:
        name, words, why = parse_alias(text_)
        assert len(name) > 8 and why, f"{name}: a long name and an explanation"  # `first-import` is the shortest
        help_text = help_of(words[0])
        for flag in (w for w in words if w.startswith("--")):
            assert re.search(rf"(?<![\w-]){re.escape(flag.split('=')[0])}(?![\w-])", help_text), f"{name}: {flag}"
