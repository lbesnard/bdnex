"""``bdnex check``: is the library consistent with itself and with the disk?"""

from __future__ import annotations

import argparse
import sys

from bdnex.config import ConfigError, load_config
from bdnex.library import Library
from bdnex.library.health import check, fix
from bdnex.ui import color


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex check", description=__doc__,
        epilog="Looks at the database (integrity, links between tables), at the files it points to (missing, changed), "
               "at covers, the trash and leftovers of interrupted writes. Nothing is changed unless you add --fix, "
               "which only settles what is safe: empty series, dead trash records, covers that are gone, temporary files.")
    parser.add_argument("--fix", action="store_true", help="settle what is safe to settle (listed as [fixable])")
    args = parser.parse_args(argv)
    try:
        settings = load_config().settings
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    if not settings.library_path.exists():
        print(f"No library yet ({settings.library_path}).", file=sys.stderr)
        return 1

    on = color.enabled(out)
    with Library.for_settings(settings, readonly=not args.fix) as library:
        findings = check(library, settings)
        if not findings:
            print(color.paint("The library is consistent.", "green", on), file=out)
            return 0
        for finding in findings:
            tag = " [fixable]" if finding.fixable else ""
            print(f"\n{color.paint('●', 'yellow', on)} {color.paint(finding.problem, 'bold', on)}{tag}", file=out)
            for item in finding.items:
                print(f"    {item}", file=out)
            if finding.advice:
                print(f"    {color.paint(finding.advice, 'dim', on)}", file=out)
        if args.fix:
            done = fix(library, settings, findings)
            print("", file=out)
            for line in done or ["nothing could be settled automatically"]:
                print(color.paint(f"fixed: {line}", "green", on), file=out)
            return 0
    fixable = sum(1 for f in findings if f.fixable)
    print(f"\n{len(findings)} thing(s) to look at." + (f" `bdnex check --fix` settles the {fixable} marked [fixable]." if fixable else ""),
          file=out)
    return 1
