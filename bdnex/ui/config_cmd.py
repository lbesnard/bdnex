"""``bdnex config``: inspect and edit the configuration."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys

import yaml

from bdnex.config import ConfigError, load_config, load_defaults, user_config_path


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bdnex config", description="Show or edit the bdnex configuration")
    parser.add_argument("-p", "--path", action="store_true", help="print the config file path")
    parser.add_argument("-e", "--edit", action="store_true", help="open the config file in $EDITOR")
    parser.add_argument("-d", "--defaults", action="store_true", help="print every option with its default")
    args = parser.parse_args(argv)

    if args.defaults:
        print(yaml.safe_dump(load_defaults(), sort_keys=False, allow_unicode=True), end="")
        return 0

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
    print(yaml.safe_dump(loaded.raw, sort_keys=False, allow_unicode=True), end="")
    return 0


def _edit(path) -> int:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    return subprocess.call(shlex.split(editor) + [str(path)])
