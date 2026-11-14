"""``bdnex edit``: change the metadata of albums or series by hand.

The albums found by a query are opened as YAML in ``$VISUAL`` / ``$EDITOR``; what you change is
checked, shown, and stored. Your edits are kept on top of what the source says: a re-import,
``--recache`` or ``reparse`` does not undo them.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from typing import Any, Callable

import yaml

from bdnex.library import Library
from bdnex.library.query import QueryError
from bdnex.ui import context, describe
from bdnex.ui.output import add_pretend

INT_FIELDS = {"volume": (0, 9999), "year": (0, 9999), "month": (1, 12), "day": (1, 31), "pages": (0, 99999)}
TEXT_FIELDS = ("title", "alternate_number", "publisher", "language", "format", "isbn", "summary")
ORDER = ("series", "volume", "title", "alternate_number", "publisher", "year", "month", "day", "pages",
         "language", "format", "isbn", "summary", "credits", "genres")

HEADER = """\
# bdnex edit: change the values you want, save and close the editor.
#   - leave a line alone to keep it; `null` (or an empty value) clears a field;
#   - credits: a role you list is replaced by what you write (`Writer: []` removes the role);
#   - `series:` renames the series for ALL its albums;
#   - delete the whole entry of an album to leave it untouched.
# Your edits are kept when the album is later re-imported or re-read from its page.
"""


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, value):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def album_document(album: dict[str, Any]) -> dict[str, Any]:
    doc: dict[str, Any] = {"id": album["id"]}
    for key in ORDER:
        if key == "series" and album["kind"] != "series":
            continue
        if key == "credits":
            doc[key] = {role: list(names) for role, names in album["credits"].items()}
        elif key == "genres":
            doc[key] = list(album["genres"])
        else:
            doc[key] = album.get(key)
    return doc


def render(albums: list[dict[str, Any]]) -> str:
    text = HEADER
    for album in albums:
        text += f"\n# {describe.label(album)}\n"
        text += yaml.dump([album_document(album)], Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                          default_flow_style=None, width=100)
    return text


def _scalar(key: str, value: Any, errors: list[str], where: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if key in INT_FIELDS:
        lo, hi = INT_FIELDS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            errors.append(f"{where}: {key} must be a whole number between {lo} and {hi}, not {value!r}")
            return None
        return value
    if isinstance(value, (dict, list)) or isinstance(value, bool):
        errors.append(f"{where}: {key} must be text, not {value!r}")
        return None
    return str(value).strip()


def _names(value: Any, errors: list[str], where: str) -> list[str]:
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(i, (str, int, float)) for i in items):
        errors.append(f"{where}: expected a list of names, not {value!r}")
        return []
    return [str(i).strip() for i in items if str(i).strip()]


PROBLEMS_MARK = "# ---- fix these"


def with_problems(text: str, problems: list[str]) -> str:
    """``text`` with the problems listed at the top, replacing an earlier list."""
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith(PROBLEMS_MARK):
        end = next((i for i, line in enumerate(lines) if line == "#\n"), -1)
        lines = lines[end + 1:]
    head = [f"{PROBLEMS_MARK} and save again (leave the file unchanged to cancel):\n"]
    head += [f"# - {problem}\n" for problem in problems] + ["#\n"]
    return "".join(head + lines)


def doc_changes(album: dict[str, Any], doc: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    """What ``doc`` (the edited form of ``album``) changes. Fields missing from ``doc`` are kept."""
    where = describe.label(album)
    change: dict[str, Any] = {}
    unknown = set(doc) - {"id", *ORDER}
    if unknown:
        errors.append(f"{where}: unknown field(s) {', '.join(sorted(map(str, unknown)))}")
    for key in ORDER:
        if key not in doc:
            continue  # a deleted line means "keep"
        value = doc[key]
        if key == "series":
            if album["kind"] != "series":
                errors.append(f"{where}: a one-shot has no series")
                continue
            name = _scalar(key, value, errors, where)
            if not name:
                errors.append(f"{where}: series can not be empty")
            elif name != album["series"]:
                change["series"] = name
        elif key == "credits":
            if not isinstance(value, dict):
                errors.append(f"{where}: credits must be `Role: [names]` lines")
                continue
            new = {str(role): _names(names, errors, f"{where}: {role}") for role, names in value.items()}
            diff = {role: names for role, names in new.items() if names != album["credits"].get(role, [])}
            if diff:
                change["credits"] = diff
        elif key == "genres":
            genres = _names(value, errors, f"{where}: genres")
            if genres != album["genres"]:
                change["genres"] = genres
        else:
            new_value = _scalar(key, value, errors, where)
            if new_value != (album.get(key) or None):
                change[key] = new_value
    return change


def parse(text: str, albums: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], list[str]]:
    """Turn the edited text into ``{album_id: changes}`` (only what differs) and a list of problems."""
    try:
        docs = yaml.safe_load(text)
    except yaml.YAMLError as err:
        return {}, [f"not valid YAML: {str(err).splitlines()[0] if str(err) else err}"]
    if docs is None:
        return {}, []
    if not isinstance(docs, list) or not all(isinstance(d, dict) for d in docs):
        return {}, ["the file must stay a list of entries, each starting with `- id: ...`"]
    errors: list[str] = []
    by_id = {a["id"]: a for a in albums}
    changes: dict[int, dict[str, Any]] = {}
    seen: set = set()
    for doc in docs:
        album = by_id.get(doc.get("id"))
        if album is None:
            errors.append(f"unknown id {doc.get('id')!r}: do not add or change the `id` lines")
            continue
        if album["id"] in seen:
            errors.append(f"{describe.label(album)}: listed twice")
            continue
        seen.add(album["id"])
        change = doc_changes(album, doc, errors)
        if change:
            changes[album["id"]] = change
    return changes, errors


def describe_changes(albums: dict[int, dict[str, Any]], changes: dict[int, dict[str, Any]]) -> list[str]:
    lines = []
    for album_id, change in changes.items():
        album = albums[album_id]
        lines.append(describe.label(album))
        for key, value in change.items():
            if key == "credits":
                for role, names in value.items():
                    lines.append(f"    {role}: {', '.join(album['credits'].get(role, [])) or '-'} -> {', '.join(names) or '-'}")
            elif key == "genres":
                lines.append(f"    genres: {', '.join(album['genres']) or '-'} -> {', '.join(value) or '-'}")
            else:
                suffix = "  (renames the series for all its albums)" if key == "series" else ""
                lines.append(f"    {key}: {album.get(key)!r} -> {value!r}{suffix}")
    return lines


def parse_assignments(items: list[str]) -> dict[str, Any]:
    """``--set publisher=Soleil --set genres="Humour; Fantasy" --set credits.Writer="A; B"``."""
    changes: dict[str, Any] = {}
    for item in items:
        key, sep, value = item.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            raise ValueError(f"{item!r}: use KEY=VALUE")
        if key.startswith("credits."):
            changes.setdefault("credits", {})[key.split(".", 1)[1]] = [n.strip() for n in value.split(";") if n.strip()]
        elif key == "genres":
            changes["genres"] = [g.strip() for g in value.split(";") if g.strip()]
        elif key in INT_FIELDS:
            if value and not value.isdigit():
                raise ValueError(f"{key} must be a whole number, not {value!r}")
            changes[key] = int(value) if value else None
        elif key == "series" or key in TEXT_FIELDS:
            changes[key] = value or None
        else:
            raise ValueError(f"unknown field {key!r} (fields: {', '.join(ORDER)}; credits.ROLE)")
    return changes


def open_editor(text: str) -> str:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        path = handle.name
    try:
        subprocess.run([*shlex.split(editor), path], check=True)
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    finally:
        os.unlink(path)


def run(argv: list[str], out=sys.stdout, editor: Callable[[str], str] = open_editor, prompter=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bdnex edit", description=__doc__.split("\n\n")[0],
        epilog="Afterwards, `bdnex write <same query>` puts the new values into the files.")
    parser.add_argument("query", nargs="+", help="which albums, as in `bdnex ls` (e.g. series:nains)")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="change a field of every matching album without opening the editor; repeatable. "
                             "Lists are separated by ';': --set genres='Humour; Fantasy' --set credits.Writer='A; B'")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    add_pretend(parser, "change")
    args = parser.parse_args(argv)

    settings = context.settings()
    if settings is None:
        return 1
    try:
        assignments = parse_assignments(args.set)
    except ValueError as err:
        print(f"bad --set: {err}", file=sys.stderr)
        return 2

    with Library.for_settings(settings) as library:
        try:
            rows = [r for r in library.query(args.query) if r.get("id")]
        except QueryError as err:
            return context.bad_query(err)
        albums = [library.get_album(r["id"]) for r in rows]
        if not albums:
            print("No album matches that query.", file=sys.stderr)
            return 1
        by_id = {a["id"]: a for a in albums}

        if assignments:
            changes, problems = {}, []
            for album in albums:
                if "series" in assignments and album["kind"] != "series":
                    print(f"{describe.label(album)}: a one-shot has no series; skipped", file=sys.stderr)
                    continue
                change = doc_changes(album, dict(assignments), problems)
                if change:
                    changes[album["id"]] = change
            if problems:
                print("\n".join(problems), file=sys.stderr)
                return 2
        else:
            current = render(albums)
            while True:
                edited = editor(current)
                changes, problems = parse(edited, albums)
                if not problems:
                    break
                if edited == current:
                    print("Cancelled: nothing changed.", file=out)
                    return 1
                current = with_problems(edited, problems)

        if not changes:
            print("Nothing changed.", file=out)
            return 0
        print("\n".join(describe_changes(by_id, changes)), file=out)
        if args.pretend:
            return 0
        if not args.yes:
            if prompter is None:
                from bdnex.ui.prompts import new_prompter
                prompter = new_prompter()
            if not prompter.confirm(f"Apply these changes to {len(changes)} album(s)?"):
                print("Nothing changed.", file=out)
                return 1
        for album_id, change in changes.items():
            library.edit_album(album_id, change)
        library.prune_orphan_series()
        print(f"\n{len(changes)} album(s) updated. `bdnex write` puts the new values into the files.", file=out)
    return 0
