"""Every command says the same thing, with the same exit code, when the library or the query is missing."""

import pytest

from bdnex import ui
from bdnex.ui import context


@pytest.mark.parametrize("command", ["ls", "check", "duplicates", "undo", "review", "series", "trash", "reparse", "relocate OLD NEW"])
def test_a_missing_library_is_one_message_and_exit_code_1(command, capsys):
    with pytest.raises(SystemExit) as stop:
        ui.main(command.split())
    err = capsys.readouterr().err
    assert stop.value.code == 1 and "No library yet (" in err and "run `bdnex import <folder>` first." in err


def test_the_reason_for_needing_a_library_can_be_given(capsys):
    assert context.settings(doing="nothing to reset.") is None
    assert "nothing to reset." in capsys.readouterr().err


def test_a_command_that_needs_no_library_gets_its_settings():
    assert context.settings(library=False) is not None


def test_a_bad_query_is_exit_code_2(capsys):
    assert context.bad_query(ValueError("no such field")) == 2
    assert "bad query: no such field" in capsys.readouterr().err
