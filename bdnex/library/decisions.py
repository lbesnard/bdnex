"""A file of decisions about matches, applied the way ``bdnex review`` applies them (``bdnex review --apply FILE``).

For an assistant or a script that read the evidence (``bdnex review --auto -n --json``, ``bdnex candidates --json``) and
decided: it writes one decision per file, and this module is the one place that turns them into changes, through the
same steps as the review screen (a ``checked`` mark, the flag, the link), so nothing is done by hand on the database.

A line is ``path<TAB>decision<TAB>album<TAB>reason`` or a JSON object with those keys; ``#`` starts a comment line.
``decision``: ``keep`` (the match is right: the flag goes), ``unlink`` (the file waits for ``import --skipped``),
``switch`` (link to ``album``, an address or a number), ``leave`` (decided later: marked, nothing else changes).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from bdnex.library import Library

DECISIONS = ("keep", "unlink", "switch", "leave")


class DecisionError(ValueError):
    """A line that can not be understood: its number and why."""


@dataclass
class Decision:
    path: str
    action: str
    album: str = ""
    reason: str = ""
    line: int = 0


def parse(text: str) -> list[Decision]:
    """The decisions of a file. A bad line raises ``DecisionError`` (nothing is applied from a file that does not parse)."""
    found: list[Decision] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("{"):
            try:
                data = json.loads(line)
            except ValueError as err:
                raise DecisionError(f"line {number}: not valid JSON ({err})") from None
            fields = [str(data.get(key) or "") for key in ("path", "decision", "album", "reason")]
        else:
            fields = (line.split("\t") + ["", "", "", ""])[:4]
            if fields[0] == "path" and fields[1] == "decision":  # the header line
                continue
        path, action, album, reason = (field.strip() for field in fields)
        if not path or action not in DECISIONS:
            raise DecisionError(f"line {number}: `{action}` is not one of {', '.join(DECISIONS)} (or the path is empty)")
        if action == "switch" and not album:
            raise DecisionError(f"line {number}: `switch` needs the album (an address or a number)")
        found.append(Decision(path, action, album, reason, number))
    return found


def apply(library: Library, decisions: list[Decision], pretend: bool = False, say=lambda text: None) -> dict[str, int]:
    """Apply the decisions one by one; ``say`` is told about each. A file that is not in the library, or a switch the
    library refuses (the album has another file), changes nothing and is counted. ``pretend``: only count."""
    from bdnex.library.review_batch import Row, decide, folder_of, link_to

    count = {"keep": 0, "unlink": 0, "switch": 0, "leave": 0, "refused": 0, "unknown": 0}
    for item in decisions:
        file = library.get_file(item.path)
        if file is None:
            count["unknown"] += 1
            say(f"not in the library: {item.path}")
            continue
        album = library.get_album(file["album_id"]) if file.get("album_id") is not None else None
        row = Row(item.path, folder_of(item.path), None, file.get("album_id"), None, None, file.get("cover_score"), "ok", file_id=file.get("id"))
        if album:
            row.album, row.album_url = album.get("title"), album.get("url")
        if item.action == "switch" and not pretend:
            done, message = link_to(library, row, item.album)
            if not done:
                count["refused"] += 1
                say(f"{item.path}: {message}")
                continue
        elif item.action != "switch" and not pretend:
            decide(library, row, item.action)
        count[item.action] += 1
        say(f"{item.action:<6} {item.path}" + (f"  -> {item.album}" if item.album else "") + (f"  ({item.reason})" if item.reason else ""))
    return count


def read(path: str) -> Optional[list[Decision]]:
    with open(path, encoding="utf-8") as handle:
        return parse(handle.read())
