"""``bdnex stats`` and ``bdnex fields``: what the library holds, and what a query can say."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from bdnex.formatting import human_size
from bdnex.library import Library
from bdnex.library import query as q
from bdnex.library.query import QueryError
from bdnex.ui import context

# One line per query field (a test fails when a field has none: the list of fields can not outgrow its help).
FIELD_HELP = {
    "any": "the text in any text field at once", "attr": "your own marks: attr:name or attr:name=value (`bdnex mark`)", "author": "any credit (any role)", "colorist": "a credit with that role",
    "coverartist": "a credit with that role", "inker": "a credit with that role", "letterer": "a credit with that role",
    "penciller": "a credit with that role", "writer": "a credit with that role",
    "complete": "the album's page was read", "partial": "only the sitemap address is known (the page was not read)",
    "corrupted": "the file is damaged", "cover": "similarity of the file's cover with the album's (0-100)",
    "embedded": "text of the ComicInfo.xml the file already had", "fileid": "the file's number in the library",
    "filename": "the file's name", "filepages": "number of pages of the file", "files": "number of files of the album",
    "format": "cbz, cbr or pdf", "genre": "a genre of the album", "id": "the album's number in the library",
    "isbn": "ISBN", "kind": "series or oneshot", "language": "language code", "md5": "the file's checksum",
    "metadata": "the file already had a ComicInfo.xml", "pages": "pages of the album (the source's count)",
    "path": "the file's path", "publisher": "publisher", "rating": "the source's rating", "review": "flagged for `bdnex review`",
    "series": "series name", "size": "the file's size in bytes", "source": "where the album comes from",
    "spare": "a spare version of an album that has another main file", "staged": "waiting to be moved into the collection",
    "summary": "the summary", "tagged": "(files) linked to an album", "title": "the album's title", "url": "the album's page address",
    "volume": "the volume number", "year": "year of publication",
}


def kind_of(name: str) -> str:
    if name in {**q.NUMERIC_FIELDS, **q.FILE_NUMERIC_FIELDS} or name == "files":
        return "number"
    if name in {**q.BOOL_FIELDS, **q.FILE_BOOL_FIELDS}:
        return "yes/no"
    return "text"


def summary(library: Library, query: list[str]) -> dict:
    """What the library holds (``bdnex stats --json``, and ``GET /api/stats``). Raises ``QueryError``."""
    albums = library.query(query)
    files = library.query(query, files=True)
    cache = library.cache_summary()
    flagged = sum(1 for f in files if f.get("review"))
    complete = sum(1 for a in albums if not a["partial"])
    kinds = Counter(a["kind"] for a in albums)
    by_format = Counter(f["format"] for f in files)
    size_by_format = Counter()
    for f in files:
        size_by_format[f["format"]] += f["size"] or 0
    linked = sum(1 for f in files if f.get("album_id") is not None or f.get("id") is not None)
    return {
        "albums": {"total": len(albums), "complete": complete, "partial": len(albums) - complete,
                   "series": kinds.get("series", 0), "oneshots": kinds.get("oneshot", 0)},
        "files": {"total": len(files), "linked": linked, "unlinked": len(files) - linked,
                  "corrupted": sum(1 for f in files if f.get("corrupted")), "flagged_for_review": flagged,
                  "spare": sum(1 for f in files if f.get("spare")), "bytes": sum(f["size"] or 0 for f in files),
                  "by_format": {fmt: {"files": n, "bytes": size_by_format[fmt]} for fmt, n in sorted(by_format.items())}},
        "stored_pages": {kind: {"entries": n, "bytes": size} for kind, (n, size) in sorted(cache.items())},
    }


def field_list() -> list[dict]:
    """The fields a query can use, with their kind and help (``bdnex fields``, and ``GET /api/fields``)."""
    return [{"name": name, "kind": kind_of(name), "help": FIELD_HELP.get(name, "")} for name in q.ALL_FIELDS]


def fields_run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="bdnex fields", description="List the fields a query can use, with their kind.",
                                     epilog="Use them as `field:value` in `bdnex ls`, `review`, `write`, `komga`... "
                                            "(see the page *Querying the library*). `year-` or `title+` sorts.")
    parser.parse_args(argv)
    width = max(len(name) for name in q.ALL_FIELDS)
    for name in q.ALL_FIELDS:
        print(f"{name:<{width}}  {kind_of(name):<7} {FIELD_HELP.get(name, '')}", file=out)
    return 0


def run(argv: list[str], out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex stats", description="How much the library holds: albums (read or only matched), files, formats, sizes.",
        epilog="Without a query the whole library; with one (the terms of `bdnex ls`) only the albums and files it matches. "
               "Read-only, no file is touched.")
    parser.add_argument("query", nargs="*", help="only these albums and files, same terms as `bdnex ls`")
    parser.add_argument("--json", action="store_true", help="the numbers as JSON")
    args = parser.parse_args(argv)
    settings = context.settings()
    if settings is None:
        return 1
    if not context.has_library(settings, "nothing to count."):
        return 1
    with Library.for_settings(settings, readonly=True) as library:
        try:
            data = summary(library, args.query)
        except QueryError as err:
            return context.bad_query(err)
    if args.json:
        print(json.dumps(data, indent=2), file=out)
        return 0
    a, f = data["albums"], data["files"]
    share = f" ({a['complete'] / a['total']:.0%})" if a["total"] else ""
    print(f"Albums: {a['total']} ({a['series']} in a series, {a['oneshots']} one-shots)", file=out)
    print(f"  page read (complete): {a['complete']}{share}; only matched (partial): {a['partial']}"
          + ("  -> `bdnex import --fetch` or `bdnex import --reparse`" if a["partial"] else ""), file=out)
    print(f"Files: {f['total']} ({human_size(f['bytes'])}); linked to an album: {f['linked']}, without: {f['unlinked']}", file=out)
    for fmt, numbers in f["by_format"].items():
        print(f"  {fmt:<4} {numbers['files']:>6} file(s)  {human_size(numbers['bytes'])}", file=out)
    extra = [text for text, n in ((f"{f['corrupted']} damaged", f["corrupted"]), (f"{f['spare']} spare versions", f["spare"]),
                                  (f"{f['flagged_for_review']} waiting for `bdnex review`", f["flagged_for_review"])) if n]
    if extra:
        print("  " + ", ".join(extra), file=out)
    if data["stored_pages"]:
        pages = ", ".join(f"{numbers['entries']} {kind} ({human_size(numbers['bytes'])})" for kind, numbers in data["stored_pages"].items())
        print(f"Stored pages and covers: {pages}", file=out)
    return 0
