"""``bdnex check``: is the library consistent with itself and with the disk?"""

from __future__ import annotations

import argparse
import sys

from bdnex.library import Library
from bdnex.library.health import check, fix
from bdnex.ui import color, context, results
from bdnex.ui.output import add_workers


def _quality(args, settings, out) -> int:
    """``--quality``: the dpi of the files that have none yet (or all of them, with ``--again``), several files at once."""
    from bdnex import quality
    from bdnex.library import quality_scan
    from bdnex.library.query import QueryError
    from bdnex.organizer.organizer import auto_workers

    on = color.enabled(out)
    with Library.for_settings(settings) as library:
        try:
            rows = quality_scan.pending(library, args.query, args.again)
        except QueryError as err:
            return context.bad_query(err)
        if not rows:
            print(color.paint("Every file has its resolution already" + ("" if args.again else " (`--again` measures them again)") + ".", "green", on), file=out)
            return 0
        workers = (args.workers if args.workers is not None else settings.write.workers) or auto_workers()
        print(f"Measuring {len(rows)} file(s), {workers} at a time (page height {settings.quality.page_height_in} in for a CBZ / CBR)...", file=out, flush=True)
        step = max(1, len(rows) // 10)
        counts = quality_scan.measure_rows(
            library, rows, workers, settings.quality.page_height_in,
            progress=lambda number, total: print(f"  {number}/{total}", file=out, flush=True) if number % step == 0 and number != total else None)
    for name, _, text in quality.TIERS:
        print(f"{counts.get(name, 0):>7}  {name}  ({text})", file=out)
    if counts.get(quality.NOT_MEASURED):
        print(f"{counts[quality.NOT_MEASURED]:>7}  {quality.NOT_MEASURED}  (no pictures to measure: drawn with vectors, or unreadable)", file=out)
    print(color.hints("`bdnex review`: the Quality filter lists them; `bdnex ls --files dpi:..120` too.", on), file=out)
    return 0


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex check", description=__doc__,
        epilog="Looks at the database (integrity, links between tables), at the files it points to (missing, changed), "
               "at covers, the trash and leftovers of interrupted writes. Nothing is changed unless you add --fix, "
               "which only settles what is safe: empty series, dead trash records, covers that are gone, temporary files "
               "(and, with --matches, flags the doubtful matches for `bdnex review`).")
    parser.add_argument("--matches", action="store_true",
                        help="also weigh every match against its album's page (the file's own tags, volume, year, authors, "
                             "its folder) and list the ones that look wrong; with --fix they are flagged for `bdnex review`")
    parser.add_argument("--fix", action="store_true",
                        help="apply the fixes listed as [fixable]: remove empty series and dead trash records, forget cover "
                             "records whose file is gone, move covers into the per-album layout; with --matches, also flag "
                             "doubtful matches for `bdnex review`")
    parser.add_argument("--quality", action="store_true",
                        help="measure how sharp the files are (dpi: a PDF's own, a CBZ / CBR's estimated from its page height, `quality.page_height_in`) "
                             "and store it; new files are measured when they are imported, this fills in the ones that are already in the library "
                             "(only the ones with no number yet unless --again); a query limits the files")
    parser.add_argument("--again", action="store_true", help="with --quality: measure the files that already have a number too (after changing `quality.page_height_in`)")
    add_workers(parser, "files measured", "write.workers", ", at most 4")
    parser.add_argument("query", nargs="*", help="with --quality: only these files, same terms as `bdnex ls --files` (e.g. series:nains)")
    args = parser.parse_args(argv)
    settings = context.settings()
    if settings is None:
        return 1
    if args.quality:
        return _quality(args, settings, out)
    if args.again or args.query:
        print("--again and a query are for --quality.", file=sys.stderr)
        return 2

    on = color.enabled(out)
    with Library.for_settings(settings, readonly=not args.fix) as library:
        findings = check(library, settings, matches=args.matches)
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
