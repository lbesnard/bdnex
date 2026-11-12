"""``bdnex completion bash|zsh|fish``: a tab-completion script for the shell.

The commands come from the usage text and each command's options from its own ``--help``, so the script can
not drift from the program.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys

SHELLS = ("bash", "zsh", "fish")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+|-[a-zA-Z])(?=[\s,=\[]|$)")


def commands() -> list[str]:
    from bdnex.ui import USAGE

    return re.findall(r"^  ([a-z]+)\s", USAGE, re.M)


def options(command: str) -> list[str]:
    """The flags a command accepts, read from its ``--help``."""
    from bdnex import ui

    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            ui.main([command, "--help"])
    except SystemExit:
        pass
    return sorted(set(FLAG.findall(out.getvalue())) - {"-h"})


def script(shell: str) -> str:
    table = {command: options(command) for command in commands() if command != "completion"}
    names = " ".join([*table, "completion"])
    if shell == "fish":
        lines = [f"complete -c bdnex -f -n __fish_use_subcommand -a '{names}'"]
        for command, flags in table.items():
            for flag in flags:
                kind = f"-l {flag[2:]}" if flag.startswith("--") else f"-s {flag[1:]}"
                lines.append(f"complete -c bdnex -n '__fish_seen_subcommand_from {command}' {kind}")
        lines.append("complete -c bdnex -n '__fish_seen_subcommand_from completion' -a 'bash zsh fish'")
        return "\n".join(lines) + "\n"
    cases = "\n".join(f"    {command}) opts='{' '.join(flags)}' ;;" for command, flags in table.items())
    body = f"""_bdnex() {{
  local cur="${{COMP_WORDS[COMP_CWORD]}}" opts=""
  if [ "$COMP_CWORD" -eq 1 ]; then
    COMPREPLY=($(compgen -W "{names}" -- "$cur"))
    return
  fi
  case "${{COMP_WORDS[1]}}" in
{cases}
    completion) opts='bash zsh fish' ;;
  esac
  if [[ "$cur" == -* || "${{COMP_WORDS[1]}}" == completion ]]; then
    COMPREPLY=($(compgen -W "$opts" -- "$cur"))
  else
    COMPREPLY=($(compgen -f -- "$cur"))
  fi
}}
complete -o filenames -o bashdefault -F _bdnex bdnex
"""
    if shell == "zsh":
        return "autoload -U +X bashcompinit && bashcompinit\n" + body
    return body


def run(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="bdnex completion",
        description="Print a tab-completion script: commands, options and file names. "
                    "Load it with `eval \"$(bdnex completion zsh)\"` in your shell's startup file.")
    parser.add_argument("shell", choices=SHELLS)
    args = parser.parse_args(argv)
    sys.stdout.write(script(args.shell))
    return 0
