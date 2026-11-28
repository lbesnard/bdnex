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
    parser.add_argument("--fields", metavar="LIST",
                        help="push only these fields, comma separated (the others are left as Komga has them): "
                             + ", ".join(FIELD_NAMES))
    parser.add_argument("--no-lock", action="store_true",
                        help="push: do not lock the fields sent (a Komga rescan may then undo them); default: komga.lock")
    parser.add_argument("--with-review", action="store_true",
                        help="push: also send the files whose match waits for a look (`bdnex review`); by default they are "
                             "left out, so that a doubtful match never puts wrong metadata (and a lock) in Komga")
    parser.add_argument("--overwrite-locked", action="store_true",
                        help="push: also change the fields that are locked in Komga (by default a locked field that differs is left alone: "
                             "someone locked it there on purpose, or an earlier push did and it was changed since)")
    parser.add_argument("--json", action="store_true", help="show: print what Komga holds as JSON")
    parser.add_argument("--missing", action="store_true",
                        help="show: list the Komga books that no library file matches (instead of the matches)")
    parser.add_argument("-y", "--yes", action="store_true", help="push: do not ask for confirmation")
    add_pretend(parser, "be sent to Komga (push)")
    add_verbosity(parser)
    args = parser.parse_intermixed_args(argv)  # `push -y series:x` and `push series:x -y`, on every Python
    setup_logging(args.verbose)
    try:
        args.wanted = _wanted(args.fields)
    except ValueError as err:
        parser.error(str(err))
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
            waiting = [r for r in rows if r.get("review")]
            if waiting and args.action == "push" and not args.with_review:
                rows = [r for r in rows if not r.get("review")]  # a doubtful match must not lock wrong data in Komga
                print(f"{len(waiting)} file(s) whose match waits for a review are left out (`bdnex review`; "
                      "`--with-review` sends them anyway).", file=sys.stderr)
            print(f"Reading the books of Komga ({client.base})...", file=sys.stderr if args.action == "show" else out)
            books = list(client.books())
            found = komga.match(rows, books)
            albums = {}
            for file, _, _ in found["pairs"]:
                if file["id"] not in albums:
                    albums[file["id"]] = library.get_album(file["id"])
                    albums[file["id"]]["series_record"] = library.series_record(file["id"])
            if args.action == "show":
                return _inspect(client, found, books, albums, args, out, on)
            plan = _plan(client, found["pairs"], albums, args, lock)
    except komga.KomgaError as err:
        print(err, file=sys.stderr)
        if out is not sys.stdout:
            print(err, file=out)  # a window shows `out`, not stderr
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
        fields = komga.book_fields(album, meta)
        rows = [(key, meta.get(key), fields.get(key), meta.get(komga.BOOK_LOCKS.get(key, "")))
                for key in ("title", "number", "releaseDate", "authors", "summary", "isbn", "links", "tags")]
        if args.verbose:
            sid = book["seriesId"]
            if sid not in series_seen:
                series_seen[sid] = client.series(sid).get("metadata") or {}
            wanted = komga.series_fields([album])
            rows += [(f"series {key}", series_seen[sid].get(key), wanted.get(key),
                      series_seen[sid].get(komga.SERIES_LOCKS.get(key, "")))
                     for key in ("title", "publisher", "language", "genres", "summary", "status", "totalBookCount",
                                "links")]
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
    """A value as the table shows it: credits as ``name (role)``, links as ``label: url`` (never cut), the rest short."""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        if "url" in value[0]:
            return "; ".join(f"{v.get('label')}: {v['url']}" for v in value)
        return "; ".join(f"{v['name']} ({v['role']})" if "role" in v else str(v) for v in value)
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    return _short(value)


BOOK_FIELD_KEYS = {"title": ("title",), "number": ("number", "numberSort"), "releaseDate": ("releaseDate",),
                   "authors": ("authors",), "summary": ("summary",), "isbn": ("isbn",), "links": ("links",),
                   "tags": ("tags",)}
SERIES_FIELD_KEYS = {"series-title": "title", "publisher": "publisher", "language": "language", "genres": "genres",
                     "series-summary": "summary", "series-status": "status", "series-total": "totalBookCount",
                     "series-link": "links"}
FIELD_NAMES = [*BOOK_FIELD_KEYS, *SERIES_FIELD_KEYS]


def _wanted(text):
    """``--fields`` as ``(book keys, series keys)``; ``None`` for a side means everything. Unknown names are an error."""
    if not text:
        return None, None
    names = [n.strip() for n in text.split(",") if n.strip()]
    bad = [n for n in names if n not in FIELD_NAMES]
    if bad:
        raise ValueError(f"unknown field(s) {', '.join(bad)}: choose among {', '.join(FIELD_NAMES)}")
    books = {k for n in names if n in BOOK_FIELD_KEYS for k in BOOK_FIELD_KEYS[n]}
    series = {SERIES_FIELD_KEYS[n] for n in names if n in SERIES_FIELD_KEYS}
    return books, series


def _only(fields, keep):
    return fields if keep is None else {k: v for k, v in fields.items() if k in keep}


def _plan(client, pairs, albums, args, lock) -> list[dict]:
    """One step per book or series that differs: what to send, and what changes."""
    plan = []
    by_series: dict[str, list] = {}
    for file, book, how in pairs:
        album = albums[file["id"]]
        by_series.setdefault(book["seriesId"], []).append(album)
        if args.only == "series" or (args.wanted[0] is not None and not args.wanted[0]):
            continue
        current = book.get("metadata") or {}
        locked: list[str] = []
        payload, differing = komga.changes(_only(komga.book_fields(album, current), args.wanted[0]), current,
                                           komga.BOOK_LOCKS, lock, not getattr(args, "overwrite_locked", False), locked)
        plan.append({"kind": "book", "id": book["id"], "label": _label(album), "how": how, "payload": payload,
                     "differing": differing, "current": book.get("metadata") or {}, "locked": locked})
    if args.only != "books" and not (args.wanted[1] is not None and not args.wanted[1]):
        for series_id, group in by_series.items():
            current = client.series(series_id).get("metadata") or {}
            named = sorted({a.get("series") or a["title"] for a in group})
            if len(named) > 1:  # Komga's series is a folder: a folder of several series (or of one-shots) has no one title, summary or status
                plan.append({"kind": "series", "id": series_id, "label": ", ".join(named[:3]) + ("..." if len(named) > 3 else ""), "how": "",
                             "payload": {}, "differing": [], "current": current,
                             "skipped": f"its Komga folder holds {len(named)} different series or albums: no series data is sent"})
                continue
            fields = komga.series_fields(group, current)
            locked = []
            payload, differing = komga.changes(_only(fields, args.wanted[1]), current, komga.SERIES_LOCKS, lock,
                                               not getattr(args, "overwrite_locked", False), locked)
            plan.append({"kind": "series", "id": series_id, "label": fields["title"], "how": "", "payload": payload,
                         "differing": differing, "current": current, "locked": locked})
    return plan


def _label(album: dict) -> str:
    volume = f" T{int(album['volume']):02d}" if album.get("volume") is not None else ""
    return f"{album['series']}{volume} - {album['title']}" if album.get("series") and album["series"] != album["title"] \
        else album["title"]


def _show(plan, found, args, out, on) -> None:
    for step in plan:
        if step.get("skipped"):
            print(results.block("skipped", f"series  {step['label']}", [step["skipped"]], on), file=out)
            continue
        if not step["differing"] and not step["payload"]:
            continue
        lines = [f"{key}: {_short(step['current'].get(key))} -> {_short(step['payload'][key])}"
                 for key in step["differing"]] if (args.verbose > 0 or args.pretend) else []
        title = f"{step['kind']}  {step['label']}" + (f"  ({', '.join(step['differing'])})" if not lines else "")
        print(results.block("would update", title, lines, on), file=out)
    n_books = sum(1 for s in plan if s["kind"] == "book" and s["payload"])
    n_series = sum(1 for s in plan if s["kind"] == "series" and s["payload"])
    held = sum(len(s.get("locked") or []) for s in plan)
    same = sum(1 for s in plan if not s["payload"] and not s.get("skipped"))
    parts = [(f"{n_books} book(s) and {n_series} series to update", "would update"),
             (f"{same} already up to date", "unchanged")]
    if held:
        parts.append((f"{held} field(s) locked in Komga left alone (`--overwrite-locked` changes them)", "skipped"))
    if found["no_book"]:
        parts.append((f"{len(found['no_book'])} file(s) not in Komga (not scanned yet, or another size)", "skipped"))
    if found["ambiguous"]:
        parts.append((f"{len(found['ambiguous'])} ambiguous (same name twice)", "ambiguous"))
    print(results.footer(parts, on), file=out)


def _short(value) -> str:
    text = " ".join(str(value if value not in (None, "") else "-").split())
    return text if len(text) <= 60 else text[:57] + "..."


GIVE_UP_AFTER = 5  # failures in a row: the server is down, or the key is refused: asking the other thousands would only repeat it


def _send(client, todo, out, on) -> int:
    failed = row = sent = 0
    for number, step in enumerate(todo):
        send = client.patch_book if step["kind"] == "book" else client.patch_series
        try:
            left_out = send(step["id"], step["payload"])
            if left_out:  # Komga refused these values (an invalid ISBN...): the rest was sent
                print(results.block("skipped", f"{step['kind']}  {step['label']}", [f"Komga refused {', '.join(left_out)}: left out, the rest was sent"], on), file=out)
            sent += 1
            row = 0
        except komga.KomgaError as err:
            failed += 1
            row += 1
            print(results.block("failed", f"{step['kind']}  {step['label']}", [str(err)], on), file=out)
            if row >= GIVE_UP_AFTER:
                left = len(todo) - number - 1
                print(f"{GIVE_UP_AFTER} failures in a row: stopped, {left} more not tried.", file=out)
                break
    print(f"Sent to Komga: {color.paint(str(sent), 'green_bold', on)} of {len(todo)}.", file=out)
    if failed or sent < len(todo):  # (a push is not one transaction: what was sent stays; `push` only sends what differs, so again finishes it)
        print(color.hints("Run `bdnex komga push` again: it sends only what still differs, so it finishes the rest.", on), file=out)
    return 1 if failed or sent < len(todo) else 0
