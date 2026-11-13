"""``bdnex check``: is the library consistent with itself and with the disk?"""

from __future__ import annotations

import argparse
import sys

from bdnex.library import Library
from bdnex.library.health import check, fix
from bdnex.ui import color, context, results


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex check", description=__doc__,
        epilog="Looks at the database (integrity, links between tables), at the files it points to (missing, changed), "
               "at covers, the trash and leftovers of interrupted writes. Nothing is changed unless you add --fix, "
               "which only settles what is safe: empty series, dead trash records, covers that are gone, temporary files.")
    parser.add_argument("--fix", action="store_true", help="settle what is safe to settle (listed as [fixable])")
    args = parser.parse_args(argv)
    settings = context.settings()
    if settings is None:
        return 1

    on = color.enabled(out)
    with Library.for_settings(settings, readonly=not args.fix) as library:
        findings = check(library, settings)
        if not findings:
            print(color.paint("The library is consistent.", "green", on), file=out)
            return 0
        for finding in findings:
            tag = " [fixable]" if finding.fixable else ""
            lines = [*finding.items, *([color.paint(finding.advice, "dim", on)] if finding.advice else [])]
            print(results.block("", f"{finding.problem}{tag}", lines, on, "problem"), file=out)
        if args.fix:
            done = fix(library, settings, findings)
            for line in done or ["nothing could be settled automatically"]:
                print(results.head("fixed" if done else "", line, on, "fixed" if done else "problem"), file=out)
            return 0
    fixable = sum(1 for f in findings if f.fixable)
    print(f"\n{len(findings)} thing(s) to look at." + (f" `bdnex check --fix` settles the {fixable} marked [fixable]." if fixable else ""),
          file=out)
    return 1
