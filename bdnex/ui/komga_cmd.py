"""``bdnex komga``: see what a Komga server holds for your files, and send the library's metadata to it."""

from __future__ import annotations

import argparse
import sys

from bdnex import komga
from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui import color, context, results
from bdnex.ui.output import add_pretend, add_verbosity, setup_logging


def run(argv: list[str], out=sys.stdout, prompter=None, session=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex komga", description=__doc__,
        epilog="Komga does not read the ComicInfo.xml inside a PDF, so `push` is how a PDF gets its title, summary, "
               "credits and the rest in Komga. A Komga book is matched to a library file by its file name and size "
               "(the folders may differ). No comic file is read or changed.")
    parser.add_argument("action", choices=("show", "push"),
                        help="show: what Komga holds for each file, next to the library's value (reads only); "
                             "push: send the library's metadata to Komga")
    parser.add_argument("query", nargs="*",
                        help="only the files of these albums, same terms as `bdnex ls`: format:pdf  author:uderzo  "
                             "series:nains  year:2010..2015")
    parser.add_argument("--only", choices=("books", "series"),
                        help="push only the books, or only the series (default: both)")
    parser.add_argument("--no-lock", action="store_true",
                        help="push: do not lock the fields sent (a Komga rescan may then undo them); default: komga.lock")
    parser.add_argument("--json", action="store_true", help="show: print what Komga holds as JSON")
    parser.add_argument("--missing", action="store_true",
                        help="show: list the Komga books that no library file matches (instead of the matches)")
    parser.add_argument("-y", "--yes", action="store_true", help="push: do not ask for confirmation")
    add_pretend(parser, "be sent to Komga (push)")
    add_verbosity(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    settings = context.settings()
    if settings is None:
        return 1
    lock = settings.komga.lock and not args.no_lock
    on = out.isatty() if hasattr(out, "isatty") else False

    try:
        client = komga.Client(settings.komga, session=session)
        with Library.for_settings(settings, readonly=True) as library:
            try:
                rows = [r for r in library.query(args.query, files=True) if r.get("id")]
            except QueryError as err:
                return context.bad_query(err)
            print(f"Reading the books of Komga ({client.base})...", file=sys.stderr if args.action == "show" else out)
            books = list(client.books())
            found = komga.match(rows, books)
            albums = {}
            for file, _, _ in found["pairs"]:
                if file["id"] not in albums:
                    albums[file["id"]] = library.get_album(file["id"])
            if args.action == "show":
                return _inspect(client, found, books, albums, args, out, on)
            plan = _plan(client, found["pairs"], albums, args, lock)
    except komga.KomgaError as err:
        print(err, file=sys.stderr)
        return 1

    _show(plan, found, args, out, on)
    todo = [step for step in plan if step["payload"]]
    if args.pretend or not todo:
        return 0
    if not args.yes:
        if prompter is None:
            from bdnex.ui.prompts import new_prompter
            prompter = new_prompter()
        if not prompter.confirm(f"Send {len(todo)} change(s) to Komga?"):
            print("Nothing sent.", file=out)
            return 1
    return _send(client, todo, out, on)


def _inspect(client, found, books, albums, args, out, on) -> int:
    """``show``: what Komga holds for each file, next to what the library would send. Nothing is changed."""
    import json

    if args.missing:
        paired = {book["id"] for _, book, _ in found["pairs"]}
        loose = [b for b in books if b["id"] not in paired]
        if args.json:
            print(json.dumps(loose, ensure_ascii=False, indent=2), file=out)
        else:
            for book in loose:
                print(book["url"], file=out)
            print(f"{len(loose)} Komga book(s) without a library file (of {len(books)}); the query only narrows "
                  "the library's side.", file=sys.stderr)
        return 0
    if args.json:
        data = [{"file": f["path"], "album_id": f["id"], "matched_by": how, "komga": book}
                for f, book, how in found["pairs"]]
        print(json.dumps(data, ensure_ascii=False, indent=2), file=out)
        return 0
    series_seen: dict[str, dict] = {}
    for file, book, how in found["pairs"]:
        album = albums[file["id"]]
        meta = book.get("metadata") or {}
        fields = komga.book_fields(album)
        rows = [(key, meta.get(key), fields.get(key), meta.get(komga.BOOK_LOCKS.get(key, "")))
                for key in ("title", "number", "releaseDate", "authors", "summary", "isbn", "links")]
        if args.verbose:
            sid = book["seriesId"]
            if sid not in series_seen:
                series_seen[sid] = client.series(sid).get("metadata") or {}
            wanted = komga.series_fields([album])
            rows += [(f"series {key}", series_seen[sid].get(key), wanted.get(key),
                      series_seen[sid].get(komga.SERIES_LOCKS.get(key, "")))
                     for key in ("title", "publisher", "language", "genres")]
        lines = [f"file:  {file['path']}", f"komga: {book['url']}  (id {book['id']}, matched by {how})", "",
                 *_table(rows)]
        print(results.block("found", _label(album), lines, on), file=out)
    for file in found["no_book"]:
        print(results.block("skipped", file["path"], ["not in Komga (not scanned yet, or another size)"], on), file=out)
    for file in found["ambiguous"]:
        print(results.block("ambiguous", file["path"], ["several Komga books have this name"], on), file=out)
    parts = [(f"{len(found['pairs'])} in Komga", "found")]
    if found["no_book"]:
        parts.append((f"{len(found['no_book'])} not in Komga", "skipped"))
    if found["ambiguous"]:
        parts.append((f"{len(found['ambiguous'])} ambiguous", "ambiguous"))
    print(results.footer(parts, on), file=out)
    return 0


def _status(key: str, theirs, mine) -> str:
    empty = theirs in (None, "", [])
    if mine is None:
        return "bdnex has none: left alone"
    if not empty and komga._same(key.split()[-1], mine, theirs):
        return "same"
    return "push adds it" if empty else "push changes it"


def _table(rows) -> list[str]:
    """A table: one row per field, Komga's value and bdnex's value side by side, and what ``push`` would do."""
    cells = [("Field", "Komga", "bdnex", "push")]
    for key, theirs, mine, locked in rows:
        komga_value = (_value(theirs) if theirs not in (None, "", []) else "-") + (" [locked]" if locked else "")
        cells.append((key, komga_value, _value(mine) if mine is not None else "-", _status(key, theirs, mine)))
    cells = [tuple(c.replace("|", "/") for c in row) for row in cells]
    widths = [max(len(row[i]) for row in cells) for i in range(4)]
    lines = ["| " + " | ".join(c.ljust(w) for c, w in zip(row, widths)) + " |" for row in cells]
    lines.insert(1, "|" + "|".join("-" * (w + 2) for w in widths) + "|")
    return lines


def _value(value) -> str:
    if isinstance(value, list):
        value = "; ".join(f"{v['name']} ({v['role']})" if "role" in v else f"{v.get('label')}" if "label" in v
                          else str(v) for v in value) if value and isinstance(value[0], dict) else ", ".join(map(str, value))
    return _short(value)


def _plan(client, pairs, albums, args, lock) -> list[dict]:
    """One step per book or series that differs: what to send, and what changes."""
    plan = []
    by_series: dict[str, list] = {}
    for file, book, how in pairs:
        album = albums[file["id"]]
        by_series.setdefault(book["seriesId"], []).append(album)
        if args.only == "series":
            continue
        payload, differing = komga.changes(komga.book_fields(album), book.get("metadata") or {},
                                           komga.BOOK_LOCKS, lock)
        plan.append({"kind": "book", "id": book["id"], "label": _label(album), "how": how, "payload": payload,
                     "differing": differing, "current": book.get("metadata") or {}})
    if args.only != "books":
        for series_id, group in by_series.items():
            current = client.series(series_id).get("metadata") or {}
            fields = komga.series_fields(group)
            payload, differing = komga.changes(fields, current, komga.SERIES_LOCKS, lock)
            plan.append({"kind": "series", "id": series_id, "label": fields["title"], "how": "", "payload": payload,
                         "differing": differing, "current": current})
    return plan


def _label(album: dict) -> str:
    volume = f" T{int(album['volume']):02d}" if album.get("volume") is not None else ""
    return f"{album['series']}{volume} - {album['title']}" if album.get("series") and album["series"] != album["title"] \
        else album["title"]


def _show(plan, found, args, out, on) -> None:
    for step in plan:
        if not step["differing"] and not step["payload"]:
            continue
        lines = [f"{key}: {_short(step['current'].get(key))} -> {_short(step['payload'][key])}"
                 for key in step["differing"]] if (args.verbose > 0 or args.pretend) else []
        title = f"{step['kind']}  {step['label']}" + (f"  ({', '.join(step['differing'])})" if not lines else "")
        print(results.block("would update", title, lines, on), file=out)
    n_books = sum(1 for s in plan if s["kind"] == "book" and s["payload"])
    n_series = sum(1 for s in plan if s["kind"] == "series" and s["payload"])
    same = sum(1 for s in plan if not s["payload"])
    parts = [(f"{n_books} book(s) and {n_series} series to update", "would update"),
             (f"{same} already up to date", "unchanged")]
    if found["no_book"]:
        parts.append((f"{len(found['no_book'])} file(s) not in Komga (not scanned yet, or another size)", "skipped"))
    if found["ambiguous"]:
        parts.append((f"{len(found['ambiguous'])} ambiguous (same name twice)", "ambiguous"))
    print(results.footer(parts, on), file=out)


def _short(value) -> str:
    text = " ".join(str(value if value not in (None, "") else "-").split())
    return text if len(text) <= 60 else text[:57] + "..."


def _send(client, todo, out, on) -> int:
    failed = 0
    for step in todo:
        send = client.patch_book if step["kind"] == "book" else client.patch_series
        try:
            send(step["id"], step["payload"])
        except komga.KomgaError as err:
            failed += 1
            print(results.block("failed", f"{step['kind']}  {step['label']}", [str(err)], on), file=out)
    done = len(todo) - failed
    print(f"Sent to Komga: {color.paint(str(done), 'green_bold', on)} of {len(todo)}.", file=out)
    return 1 if failed else 0
