#!/usr/bin/env python3
"""bdnex command line entry point."""

import sys

USAGE = """\
usage: bdnex <command> [options]

commands:
  import   match comic files to Bédéthèque albums and record them in the library
  ls       list and search the library
  write    write ComicInfo.xml into files (converts CBR/PDF to CBZ)
  move     write tags and rename files into the library tree (--copy keeps the originals)
  reparse  re-read the pages stored in the library (offline) to fill in more details
  repair   rebuild damaged archives (wrong page checksums) as clean CBZ files
  config   show or edit the configuration

Run `bdnex <command> --help` for the options of a command.
The former `bdnex -f FILE` / `-d DIR` still work and run `bdnex import -W`.
"""


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
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)

    if argv and argv[0] == "config":
        from bdnex.ui.config_cmd import run
        sys.exit(run(argv[1:]))

    if not check_config():
        sys.exit(1)

    if argv and argv[0] in ("write", "move", "repair"):
        from bdnex.ui.write_cmd import run as run_write_move
        sys.exit(run_write_move(argv[0], argv[1:]))

    if argv and argv[0] == "reparse":
        from bdnex.ui.reparse_cmd import run as run_reparse
        sys.exit(run_reparse(argv[1:]))

    if argv and argv[0] == "ls":
        from bdnex.ui.ls_cmd import run as run_ls
        sys.exit(run_ls(argv[1:]))

    if argv and argv[0] == "import":
        from bdnex.ui.import_cmd import run as run_import
        sys.exit(run_import(argv[1:]))

    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

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
              "(`--recache` refreshes them).", file=sys.stderr)
    if paths:
        print("note: `-f` / `-d` now run `bdnex import -W` (match, record in the library, write ComicInfo.xml).",
              file=sys.stderr)
        return run_import(["-W", *paths])
    if not vargs.init:
        print(USAGE)
    return 0
