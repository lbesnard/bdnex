#!/usr/bin/env python3
"""bdnex command line entry point."""

import sys

USAGE = """\
usage: bdnex <command> [options]

commands:
  init     download or refresh the album list that file names are matched against
  import   match comic files to Bédéthèque albums and record them in the library
  ls       list and search the library
  write    write ComicInfo.xml into files (converts CBR/PDF to CBZ)
  move     write tags and rename files into the library tree (--copy keeps the originals)
  series   what you have of each series, and what is missing
  edit     change the metadata of albums or series by hand (kept across re-imports)
  trash    list, restore or empty the files that were replaced
  reset    empty the library (albums, files, edits), keep the downloaded sitemaps and pages
  relocate tell the library that your comics folder moved
  review   look at the matches whose cover looked different, one by one
  check    is the library consistent with itself and with the disk? (--fix settles the safe things)
  duplicates  the same book in several files
  undo     put back the files a `move` run renamed
  relink   find files that were moved or renamed outside bdnex, by their content
  reparse  re-read the pages stored in the library (offline) to fill in more details
  repair   rebuild damaged archives (wrong page checksums) as clean CBZ files
  convert  convert the PDF files of the library to CBZ (next to the original)
  config   show or edit the configuration
  serve    open the library in a browser (web page and JSON API; read-only unless you allow changes)
  completion  print a tab-completion script for bash, zsh or fish

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

    if argv and argv[0] == "config":
        from bdnex.ui.config_cmd import run
        sys.exit(run(argv[1:]))

    if argv and argv[0] == "serve":
        from bdnex.ui.serve_cmd import run as run_serve
        sys.exit(run_serve(argv[1:]))

    if argv and argv[0] == "completion":
        from bdnex.ui.completion_cmd import run as run_completion
        sys.exit(run_completion(argv[1:]))

    if not check_config():
        sys.exit(1)

    if argv and argv[0] in ("write", "move", "repair", "convert"):
        from bdnex.ui.write_cmd import run as run_write_move
        sys.exit(run_write_move(argv[0], argv[1:]))

    if argv and argv[0] == "init":
        from bdnex.ui.init_cmd import run as run_init
        sys.exit(run_init(argv[1:]))
    if argv and argv[0] == "series":
        from bdnex.ui.series_cmd import run as run_series
        sys.exit(run_series(argv[1:]))
    if argv and argv[0] == "edit":
        from bdnex.ui.edit_cmd import run as run_edit
        sys.exit(run_edit(argv[1:]))
    if argv and argv[0] == "trash":
        from bdnex.ui.trash_cmd import run as run_trash
        sys.exit(run_trash(argv[1:]))
    if argv and argv[0] == "reset":
        from bdnex.ui.reset_cmd import run as run_reset
        sys.exit(run_reset(argv[1:]))
    if argv and argv[0] == "relocate":
        from bdnex.ui.relocate_cmd import run as run_relocate
        sys.exit(run_relocate(argv[1:]))
    if argv and argv[0] == "review":
        from bdnex.ui.review_cmd import run as run_review
        sys.exit(run_review(argv[1:]))
    if argv and argv[0] == "check":
        from bdnex.ui.check_cmd import run as run_check
        sys.exit(run_check(argv[1:]))
    if argv and argv[0] == "duplicates":
        from bdnex.ui.duplicates_cmd import run as run_duplicates
        sys.exit(run_duplicates(argv[1:]))
    if argv and argv[0] == "undo":
        from bdnex.ui.undo_cmd import run as run_undo
        sys.exit(run_undo(argv[1:]))
    if argv and argv[0] == "relink":
        from bdnex.ui.relink_cmd import run as run_relink
        sys.exit(run_relink(argv[1:]))
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
