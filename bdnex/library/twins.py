"""Files that hold the same album: look at them side by side, and decide what each one is.

A second file for an album is a *spare version* (``files.spare``) and ``bdnex move`` sends it to the spare versions folder. It is one of:

* a **true duplicate**: trash it (it can be restored from the trash);
* a **different version** the site does not know (black and white, an older edition): :func:`keep_as_version` gives it an album of its own
  (hand-written, so it has its own number), linked to the first as its ``version`` (or ``child``); it goes on being placed in the spare
  versions folder when it is a version;
* **no duplicate at all**: it is linked to the wrong album: link it to the right one (the review screen's ``o``);
* or the **main** file is the wrong one of the two: :func:`make_main` swaps them.

Nothing here reads page data: the facts come from the library (``versions.facts_of``), and the pictures of the covers are compared only
for the two files asked about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from bdnex import trash, versions
from bdnex.formatting import human_size


@dataclass
class Twin:
    path: str
    facts: versions.FileFacts
    main: bool  # the file `bdnex move` keeps in the collection (not a spare version)


def twins_of(library, path: str) -> list[Twin]:
    """The files linked to the same album as ``path`` (itself included), the main one first. Empty: no album, or the only file."""
    file = library.get_file(path)
    if not file or file.get("album_id") is None:
        return []
    rows = library.files_for_album(file["album_id"])
    if len(rows) < 2:
        return []
    found = [Twin(row["path"], versions.facts_of(library, row["path"]), not row.get("spare")) for row in rows]  # (paths are absolute here)
    found.sort(key=lambda twin: (not twin.main, twin.path))
    return found


def facts_lines(twin: Twin, root: Optional[str] = None) -> list[str]:
    """What to show of one file: its name, then format, pages, size, state."""
    facts = twin.facts
    path = facts.path
    if root and path.startswith(root.rstrip("/") + "/"):
        path = path[len(root.rstrip("/")) + 1:]
    pages = f"{facts.pages} pages" if facts.pages is not None else "pages unknown"
    state = "damaged" if facts.corrupted else "ok"
    return [("MAIN  " if twin.main else "spare ") + path, f"      {facts.format} · {pages} · {human_size(facts.size)} · {state}"]


def verdict(twins: list[Twin], prefer: list[str]) -> list[str]:
    """What the files have in common and which one `move` would keep: the lines under the facts."""
    if len(twins) < 2:
        return []
    main = next((twin for twin in twins if twin.main), twins[0])
    lines = []
    for other in twins:
        if other is main:
            continue
        comparison = versions.compare([main.facts], other.facts, prefer)
        said = {versions.IDENTICAL: "the very same bytes: a true duplicate",
                versions.SAME_BOOK: "same page count and cover: the same book",
                versions.PROBABLY_SAME: "same page count (covers could not be compared)",
                versions.DIFFERENT: "different page count or cover: perhaps another version, or the wrong album"}[comparison.relation]
        better = "the main file" if comparison.suggestion == versions.KEEP_EXISTING else "the other file"
        lines.append(f"{other.facts.name}: {said}; better: {better} ({comparison.reason})")
    return lines


def make_main(library, path: str) -> int:
    """Make ``path`` the main file of its album: the others become spare versions. Returns how many files changed."""
    file = library.get_file(path)
    if not file or file.get("album_id") is None:
        raise ValueError("this file has no album")
    changed = 0
    for row in library.files_for_album(file["album_id"]):
        spare = 0 if row["path"] == path else 1
        if bool(row.get("spare")) != bool(spare):
            library.set_file_fields(row["path"], spare=spare)
            changed += 1
    return changed


def trash_file(library, settings, path: str, reason: str = "a true duplicate (review screen)") -> int:
    """Move the file to the trash (``bdnex trash --restore`` brings it back); the album and the other files stay. Returns the trash id."""
    file = library.get_file(path)
    if file is None:
        raise KeyError(f"{path} is not in the library")
    was_main = not file.get("spare")
    others = [r for r in library.files_for_album(file["album_id"]) if r["path"] != path] if file.get("album_id") else []
    entry = trash.move_to_trash(library, settings, path, reason, row=file)
    if was_main and others:  # the main file left: the next one takes its place
        make_main(library, others[0]["path"])
    return entry


def keep_as_version(library, path: str, relation: str = "version", note: str = "", other_is_parent: bool = True) -> int:
    """The file is another version of its album (a black and white scan, an older edition the site does not list): it gets an album of its
    own, hand-written (a new number, ``manual`` source), with the same fields as the first, linked to it. Returns the new album's id.

    ``relation``: ``"version"`` or ``"child"``. ``other_is_parent``: the first album is the parent (the default); ``False``: the new
    album is the parent and the first one its child. ``note``: put after the title (``N&B``) so the two albums read differently."""
    from bdnex.library import manual

    file = library.get_file(path)
    if not file or file.get("album_id") is None:
        raise ValueError("this file has no album to be a version of")
    first = file["album_id"]
    form = manual.form_of(library, first)
    album = library.get_album(first) or {}
    if album.get("kind") != "series":
        form["series"] = ""  # a one-shot stays one
    title = form.get("title") or album.get("title") or ""
    form["title"] = f"{title} ({note.strip()})" if note.strip() else f"{title} (version)"
    volume = album.get("volume")
    new = manual.create(library, path, form, volume)
    library.set_file_fields(path, spare=0)  # it is the main file of its own album now
    if other_is_parent:
        library.set_relation(new, first, relation)
    else:
        library.set_relation(first, new, relation)
    return new


def describe(twins: list[Twin], prefer: list[str], root: Optional[str] = None) -> list[str]:
    lines: list[str] = []
    for twin in twins:
        lines.extend(facts_lines(twin, root))
    verdicts = verdict(twins, prefer)
    if verdicts:
        lines.append("")
        lines.extend(verdicts)
    return lines


def album_link_lines(library, album_id: Optional[int]) -> list[dict[str, Any]]:
    """The version/child links of an album as lines the panel can show."""
    if album_id is None:
        return []
    related = library.related_albums(album_id)
    found = []
    if related["parent"]:
        found.append({"text": f"{related['parent']['relation']} of album {related['parent']['id']}: {related['parent']['title']}"})
    for child in related["children"]:
        found.append({"text": f"has a {child['relation']}: album {child['id']}: {child['title']}"})
    return found
