"""``bdnex komga``: send the library's metadata to a Komga server."""

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
        epilog="Komga does not read the ComicInfo.xml inside a PDF, so this is how a PDF gets its title, summary, "
               "credits and the rest in Komga. A Komga book is matched to a library file by its file name and size "
               "(the folders may differ). No comic file is read or changed.")
    parser.add_argument("query", nargs="*",
                        help="only the files of these albums, same terms as `bdnex ls`: format:pdf  author:uderzo  "
                             "series:nains  year:2010..2015")
    parser.add_argument("--only", choices=("books", "series"),
                        help="update only the books, or only the series (default: both)")
    parser.add_argument("--no-lock", action="store_true",
                        help="do not lock the fields sent (a Komga rescan may then undo them); default: komga.lock")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    add_pretend(parser, "be sent to Komga")
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
            print(f"Reading the books of Komga ({client.base})...", file=out)
            books = list(client.books())
            found = komga.match(rows, books)
            albums = {}
            for file, _, _ in found["pairs"]:
                if file["id"] not in albums:
                    albums[file["id"]] = library.get_album(file["id"])
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
