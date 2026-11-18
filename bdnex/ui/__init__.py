#!/usr/bin/env python3
"""bdnex command line entry point."""

import sys

from bdnex.ui.commands import COMMANDS, runner, usage

USAGE = usage()


def check_config():
    """Create/validate the config; tell the user on first run or when it is stale.

    Returns True when bdnex may continue.
    """
    from bdnex.config import FIRST_RUN_MESSAGE, ConfigError, load_config

    try:
        loaded = load_config()
    except ConfigError as err:
        print(err, file=sys.stderr)
        print("Fix it with `bdnex config --edit`.", file=sys.stderr)
        return False
    if loaded.first_run:
        print(FIRST_RUN_MESSAGE.format(path=loaded.path), file=sys.stderr)
    for warning in loaded.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if loaded.new_options and not loaded.first_run:
        shown = ", ".join(loaded.new_options[:4]) + ("..." if len(loaded.new_options) > 4 else "")
        print(f"note: {len(loaded.new_options)} option(s) are not in your config file yet ({shown}): "
              "`bdnex config --update` adds them, commented out.", file=sys.stderr)
    return True


def main(argv=None):
    """Run a command; problems with the library folder are explained, not dumped as a traceback."""
    from bdnex.library import LibraryRootError

    try:
        _main(sys.argv[1:] if argv is None else list(argv))
    except LibraryRootError as err:
        print(err, file=sys.stderr)
        sys.exit(1)


def _main(argv):
    command = COMMANDS.get(argv[0]) if argv else None
    if command is not None:
        if command.needs_config and not check_config():
            sys.exit(1)
        sys.exit(runner(command)(argv[1:]))
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)
    if not check_config():
        sys.exit(1)
    sys.exit(legacy(argv))


def legacy(argv):
    """The pre-library options: `-f FILE`, `-d DIR`, `-i` (kept as shortcuts for the new commands)."""
    import argparse

    from bdnex.ui.import_cmd import run as run_import

    parser = argparse.ArgumentParser(prog="bdnex", usage=USAGE, add_help=False)
    parser.add_argument("-f", "--input-file")
    parser.add_argument("-d", "--input-dir")
    parser.add_argument("-i", "--init", action="store_true")
    vargs = parser.parse_args(argv)
    paths = [p for p in (vargs.input_file, vargs.input_dir) if p]
    if vargs.init:
        print("note: `-i` is no longer needed: `bdnex import` downloads the sitemaps itself "
              "(`bdnex init` refreshes them).", file=sys.stderr)
    if paths:
        print("note: `-f` / `-d` now run `bdnex import -W` (match, record in the library, write ComicInfo.xml).",
              file=sys.stderr)
        return run_import(["-W", *paths])
    if not vargs.init:
        print(USAGE)
    return 0
