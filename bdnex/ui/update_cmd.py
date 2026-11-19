"""``bdnex update``: read the files again, for what was changed outside bdnex."""

from __future__ import annotations

import argparse
import os
import sys

from bdnex.formatting import human_size
from bdnex.library import Library
from bdnex.library.database import analyse_many
from bdnex.library.query import QueryError
from bdnex.ui import color, context, results
from bdnex.ui.output import add_pretend, add_workers


def changed_on_disk(row: dict) -> str:
    """Why the file needs reading again (``""``: it is as recorded; ``"missing"``: it is not there)."""
    try:
        stat = os.stat(row["path"])
    except OSError:
        return "missing"
    if stat.st_size != row["size"]:
        return f"size {human_size(row['size'])} -> {human_size(stat.st_size)}"
    if row.get("mtime") is not None and abs(stat.st_mtime - row["mtime"]) > 1:
        return "modified"
    return ""


def differences(before: dict, info, md5: str) -> list[str]:
    """What reading the file again found different, in words."""
    found = []
    if md5 != before["md5"]:
        found.append("content changed")
    if info.page_count != before["page_count"]:
        found.append(f"pages {before['page_count']} -> {info.page_count}")
    if bool(info.corrupted) != bool(before["corrupted"]):
        found.append("now damaged" if info.corrupted else "no longer damaged")
    if bool(info.has_metadata) != bool(before["has_metadata"]):
        found.append("its own ComicInfo.xml appeared" if info.has_metadata else "its own ComicInfo.xml is gone")
    elif (info.embedded or None) != (before.get("embedded") or None):
        found.append("its own tags changed")
    return found


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex update", description=__doc__,
        epilog="The opposite of `bdnex write`: where `write` puts the library's metadata into the files, `update` records "
               "what the files are now (size, checksum, pages, damage, their own ComicInfo.xml) after you changed them "
               "with another tool. By default only the files whose size or date differs from what was recorded are read; "
               "--all reads every file of the query. The albums are not touched, and no file is changed. A file that is "
               "gone is listed (`bdnex relocate --find` finds moved files).")
    parser.add_argument("query", nargs="*", help="only these files, same terms as `bdnex ls --files`")
    parser.add_argument("--all", action="store_true", help="read every file of the query, not only the ones that changed")
    add_pretend(parser, "be read again")
    add_workers(parser, "files read")
    args = parser.parse_args(argv)
    settings = context.settings()
    if settings is None:
        return 1
    on = color.enabled(out)
    from bdnex.importer.pipeline import resolve_workers

    with Library.for_settings(settings, readonly=args.pretend) as library:
        try:
            rows = library.query(args.query, files=True)
        except QueryError as err:
            return context.bad_query(err)
        todo, missing = [], []
        for row in rows:
            why = changed_on_disk(row)
            if why != "missing" and args.all:
                why = why or "asked for"
            if why == "missing":
                missing.append(row["path"])
            elif why:
                todo.append((row, why))
        updated = unchanged = failed = 0
        if args.pretend:
            for row, why in todo:
                print(results.head("would read", f"{row['path']}  ({why})", on), file=out)
        else:
            workers = resolve_workers(args.workers if args.workers is not None else settings.import_.workers)
            by_path = {row["path"]: row for row, _ in todo}
            for path, found in analyse_many([row["path"] for row, _ in todo], workers):
                if isinstance(found, Exception):
                    failed += 1
                    print(results.block("failed", path, [str(found)], on), file=out)
                    continue
                info, md5 = found
                library.add_analysed(info, md5)
                what = differences(by_path[path], info, md5)
                if what:
                    updated += 1
                    print(results.block("updated", path, what, on), file=out)
                else:
                    unchanged += 1
    for path in missing[:20]:
        print(results.head("missing", path, on), file=out)
    if len(missing) > 20:
        print(f"... and {len(missing) - 20} more missing files", file=out)
    verb = "would be read" if args.pretend else "read"
    print(results.footer([(f"{len(todo) if args.pretend else updated + unchanged} {verb}", "updated"),
                          *([(f"{updated} different", "updated")] if not args.pretend else []),
                          *([(f"{len(missing)} missing", "failed")] if missing else []),
                          *([(f"{failed} failed", "failed")] if failed else [])], on, f"{len(rows)} file(s): "), file=out)
    if missing:
        print("`bdnex relocate --find FOLDER` finds files that were only moved or renamed.", file=out)
    return 1 if failed else 0
