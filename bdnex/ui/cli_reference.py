"""The command reference of the docs, generated from the commands' own ``--help``.

``docs/reference/commands.md`` is this text: a test fails when it is out of date; refresh it with
``uv run python -m bdnex.ui.cli_reference > docs/reference/commands.md``. The guides explain *how to do things*; this page
is the exact list of options, so it can not drift from the program.
"""

from __future__ import annotations

import contextlib
import io
import os

from bdnex.ui.commands import GROUPS

WIDTH = "88"  # the width argparse wraps at (the same on every machine)


def help_of(name: str) -> str:
    from bdnex import ui

    previous = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = WIDTH
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
            ui.main([name, "--help"])
    finally:
        if previous is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = previous
    return out.getvalue().rstrip()


def build() -> str:
    lines = ["# Command reference", "",
             "Every command with its options, as `bdnex <command> --help` prints them (this page is generated from the program:",
             "see the guides for how to use them). `bdnex --help` lists the commands by task.", ""]
    for title, commands in GROUPS:
        lines += [f"## {title}", ""]
        for command in commands:
            lines += [f"### `bdnex {command.name}`", "", command.summary[0].upper() + command.summary[1:] + ".", "",
                      "```text", help_of(command.name), "```", ""]
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    print(build(), end="")
