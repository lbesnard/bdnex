"""Tab-completion scripts: read from the program, one per shell."""

import subprocess

import pytest

from bdnex.ui import completion_cmd


def test_commands_come_from_the_usage_text():
    found = completion_cmd.commands()
    assert {"import", "ls", "series", "completion"} <= set(found)


def test_options_come_from_the_help_of_the_command():
    flags = completion_cmd.options("import")
    assert {"--auto", "--force", "-n"} <= set(flags) and "-h" not in flags


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_every_shell_gets_a_script_naming_commands_and_options(shell):
    text = completion_cmd.script(shell)
    assert "import" in text and ("cover-check" in text)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_the_script_is_valid_shell(shell):
    result = subprocess.run(["bash", "-n"], input=completion_cmd.script(shell), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_the_command_prints_the_script(capsys):
    from bdnex import ui
    with pytest.raises(SystemExit) as stop:
        ui.main(["completion", "bash"])
    assert stop.value.code == 0 and "_bdnex()" in capsys.readouterr().out
