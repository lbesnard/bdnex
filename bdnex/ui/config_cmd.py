"""``bdnex config``: inspect and edit the configuration."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys

import yaml

from bdnex.config import ConfigError, load_config, load_defaults, masked, user_config_path


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bdnex config", description="Show or edit the bdnex configuration")
    parser.add_argument("-p", "--path", action="store_true", help="print the config file path")
    parser.add_argument("-e", "--edit", action="store_true", help="open the config file in $EDITOR")
    parser.add_argument("-u", "--update", action="store_true",
                        help="add the options your file does not list yet, commented out (a .bak copy is kept)")
    parser.add_argument("--clear", action="store_true",
                        help="show the secrets too (API keys, passwords, the fetcher address); they are hidden by default")
    parser.add_argument("-d", "--defaults", action="store_true", help="print every option with its default")
    args = parser.parse_args(argv)

    if args.defaults:
        print(yaml.safe_dump(load_defaults(), sort_keys=False, allow_unicode=True), end="")
        return 0

    if args.update:
        return _update(user_config_path())

    if args.path:  # never needs a valid file
        print(user_config_path())
        return 0

    try:
        loaded = load_config()
    except ConfigError as err:
        print(err, file=sys.stderr)
        if args.edit:  # still let the user fix the file
            return _edit(user_config_path())
        return 1

    if args.edit:
        return _edit(loaded.path)

    for warning in loaded.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    shown = loaded.raw if args.clear else masked(loaded.raw)
    print(yaml.safe_dump(shown, sort_keys=False, allow_unicode=True), end="")
    return 0


def _edit(path) -> int:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    code = subprocess.call(shlex.split(editor) + [str(path)])
    if code == 0:  # check what was saved now, not at the next command
        from bdnex.config import ConfigError, load_config

        try:
            loaded = load_config()
        except ConfigError as err:
            print(f"\nThe file you saved has a problem:\n{err}\nRun `bdnex config --edit` again to fix it.", file=sys.stderr)
            return 1
        for warning in loaded.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print("The configuration is valid.", file=sys.stderr)
    return code


def _update(path) -> int:
    from bdnex.config import ensure_user_config
    from bdnex.config_template import annotate, complete

    path, created = ensure_user_config(path)
    if created:
        print(f"Created {path}, with every option listed (commented out).")
        return 0
    text = path.read_text(encoding="utf-8")
    from bdnex.config import RENAMED_KEYS

    renamed = []
    from bdnex.config import MOVED_KEYS
    from bdnex.config_template import move_option

    for old, new_name in MOVED_KEYS.items():  # an option that moved to another section keeps its value, comment and list
        text, done = move_option(text, old, new_name)
        if done:
            renamed.append(done.rstrip("."))
    for old, new_name in RENAMED_KEYS.items():  # an option that changed its name keeps its value
        if re.search(rf"(?m)^{new_name}:", text):  # both are there: renaming would give two (the last wins); build_settings warns
            continue
        text2 = re.sub(rf"(?m)^{old}:", f"{new_name}:", text)
        if text2 != text:
            renamed.append(f"{old} -> {new_name}")
            text = text2
    new, added = complete(text)
    new, helped = annotate(new)  # the possible values come back after options that lost their comment
    if not added and not renamed and not helped:
        print(f"{path} already lists every option, with its help.")
        return 0
    from bdnex.config import write_config_file

    backup = write_config_file(path, new) or path.with_name(path.name + ".bak")
    for line in renamed:
        print(line if line.startswith(("Moved", "Dropped")) else f"Renamed the option {line}.")
    print(f"Updated {path} (the previous file is {backup.name}):")
    if added:
        print(f"Added {len(added)} option(s), commented out:")
        for name in added:
            print(f"  {name}")
    if helped:
        print(f"Put the list of possible values back after {len(helped)} option(s) that had none.")
    return 0
