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


# commands whose first word is a choice, not a file (read from their usage line, `{show,push}`, so they cannot drift)
def help_of(command: str) -> str:
    """What `bdnex COMMAND --help` prints, from the command itself: not through `ui.main`, which checks the configuration first (and
    creates it on a first run, or stops with an error: every command would then have no flags and the script would still be printed)."""
    from bdnex.ui import commands

    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            commands.runner(commands.COMMANDS[command])(["--help"])
    except SystemExit:
        pass
    except Exception:  # a command that can not even show its help: no flags for it, the others are not affected
        pass
    return out.getvalue()


def option_flags(text: str) -> list[str]:
    """The flags of the options list of a help text: the lines that start an option (`  -n, --pretend  what it does`), never a flag
    that the description or the examples under it mention (`trash --help` shows `bdnex import --match --force` in its examples)."""
    flags: set[str] = set()
    inside = False
    for line in text.split("\n"):
        if re.match(r"^(options|optional arguments):\s*$", line):
            inside = True
            continue
        if inside and line and not line.startswith(" "):
            inside = False  # the epilog and the examples start in column 0
        if inside:
            head = re.match(r"^\s{1,4}(-.*?)(?:\s{2,}|$)", line)
            if head:
                flags.update(FLAG.findall(head.group(1)))
    return sorted(flags - {"-h"})


def actions(command: str) -> list[str]:
    out = io.StringIO(help_of(command))
    _, _, positionals = out.getvalue().partition("positional arguments:")  # not an option's `--only {a,b}`
    found = re.search(r"^\s+\{([a-z][a-z,-]*)\}", positionals, re.M)
    return found.group(1).split(",") if found else []


def commands() -> list[str]:
    from bdnex.ui import USAGE

    return re.findall(r"^  ([a-z]+)\s", USAGE, re.M)


def options(command: str) -> list[str]:
    """The flags a command accepts, read from its ``--help``."""
    return option_flags(help_of(command))


def script(shell: str) -> str:
    table = {command: options(command) for command in commands() if command != "completion"}
    names = " ".join([*table, "completion"])
    words = {command: actions(command) for command in table if command != "completion"}
    words = {command: choice for command, choice in words.items() if choice}
    if shell == "fish":
        lines = [f"complete -c bdnex -f -n __fish_use_subcommand -a '{names}'"]
        for command, flags in table.items():
            for flag in flags:
                kind = f"-l {flag[2:]}" if flag.startswith("--") else f"-s {flag[1:]}"
                lines.append(f"complete -c bdnex -n '__fish_seen_subcommand_from {command}' {kind}")
        lines.append("complete -c bdnex -n '__fish_seen_subcommand_from completion' -a 'bash zsh fish'")
        lines += [f"complete -c bdnex -n '__fish_seen_subcommand_from {command}' -a '{' '.join(choice)}'"
                  for command, choice in words.items()]
        return "\n".join(lines) + "\n"
    cases = "\n".join(f"    {command}) opts='{' '.join(flags)}' ;;" for command, flags in table.items())
    choices = "\n".join(f"    {command}) COMPREPLY=($(compgen -W '{' '.join(choice)}' -- \"$cur\")); return ;;"
                        for command, choice in words.items())
    body = f"""_bdnex() {{
  local cur="${{COMP_WORDS[COMP_CWORD]}}" opts=""
  if [ "$COMP_CWORD" -eq 1 ]; then
    COMPREPLY=($(compgen -W "{names}" -- "$cur"))
    return
  fi
  if [ "$COMP_CWORD" -eq 2 ] && [[ "$cur" != -* ]]; then
    case "${{COMP_WORDS[1]}}" in
{choices}
    esac
  fi
  case "${{COMP_WORDS[1]}}" in
{cases}
    completion) opts='bash zsh fish' ;;
  esac
  if [[ "$cur" == -* || "${{COMP_WORDS[1]}}" == completion ]]; then
    COMPREPLY=($(compgen -W "$opts" -- "$cur"))
  else
    local IFS=$'\\n'  # file names are split at newlines only: `Asterix - T01 - Le Gaulois.cbz` stays one name (and no `mapfile`: bash 3 and zsh)
    COMPREPLY=($(compgen -f -- "$cur"))
  fi
}}
complete -o filenames -o bashdefault -F _bdnex bdnex
"""
    if shell == "zsh":
        # compinit first (a `.zshrc` that evals this before its own compinit would find no `complete`), then the bash layer
        return "autoload -U +X compinit && compinit -u\nautoload -U +X bashcompinit && bashcompinit\n" + body
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
