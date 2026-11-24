"""How far a folder is reviewed: the tree of the review screen, read from the library (no file is touched).

A file is *reviewed* when it has the ``checked`` mark (``bdnex review``, the review screen, ``bdnex mark``). A folder is *done* when all of its
files are; it has a *problem* when a file not reviewed yet has no album or a flagged (doubtful) match: those are the ones to look at first;
otherwise it is *partial* when some files are reviewed and *todo* when none is (every file matched, nothing doubtful: it only awaits your
look). A parent counts everything below it. Paths are the stored ones
(relative to the collection, ``@alias/...`` under a source, absolute otherwise): the same text ``Library._store`` writes.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Optional

from bdnex.library.review_batch import CHECKED

DONE, PARTIAL, TODO, PROBLEM = "done", "partial", "todo", "problem"
MANY = 80  # a folder with more children than this shows them in groups by first letter (the one-shot folders are thousands)


def status_of(total: int, checked: int, issues: int = 0) -> str:
    if total and checked >= total:
        return DONE
    if issues:
        return PROBLEM
    return PARTIAL if checked else TODO


def folder_of_stored(stored: str) -> str:
    """The folder of a stored path (``""`` for a file at the top of the collection)."""
    return posixpath.dirname(stored)


@dataclass
class FolderNode:
    name: str
    path: str  # the folder as stored (``""`` is the collection itself); a letter group has the path of its parent
    files: int = 0  # files directly in this folder
    checked: int = 0  # of those, the reviewed ones
    total: int = 0  # files in this folder and below
    below_checked: int = 0  # reviewed files in this folder and below
    issues: int = 0  # files not reviewed that have no album or a doubtful match, directly in this folder
    below_issues: int = 0  # the same, in this folder and below
    children: dict[str, "FolderNode"] = field(default_factory=dict)
    group: str = ""  # a letter group of the parent's children (not a real folder)

    @property
    def status(self) -> str:
        return status_of(self.total, self.below_checked, self.below_issues)

    def label(self) -> str:
        return f"{self.name}  {self.below_checked}/{self.total}" + (f"  !{self.below_issues}" if self.below_issues else "")

    def sorted_children(self) -> list["FolderNode"]:
        return sorted(self.children.values(), key=lambda node: node.name.casefold())

    def shown_children(self) -> list["FolderNode"]:
        """The children as the tree shows them: all of them, or, past :data:`MANY`, groups by first letter that hold them."""
        children = self.sorted_children()
        if len(children) <= MANY or self.group:
            return children
        groups: dict[str, FolderNode] = {}
        for child in children:
            letter = (child.name[:1] or "#").upper()
            letter = letter if letter.isalpha() else "#"
            node = groups.setdefault(letter, FolderNode(letter, self.path, group=letter))
            node.children[child.name] = child
            node.total += child.total
            node.below_checked += child.below_checked
            node.below_issues += child.below_issues
        return [groups[key] for key in sorted(groups)]


def progress(library) -> dict[str, list[int]]:
    """``{folder: [files, reviewed, to look at]}``, one entry per folder that holds a file directly: one query. *To look at*: not reviewed,
    and no album or a doubtful match."""
    found: dict[str, list[int]] = {}
    rows = library.conn.execute(
        "SELECT f.path, EXISTS (SELECT 1 FROM file_attrs a WHERE a.file_id = f.id AND a.key = ?) AS done, "
        "(f.album_id IS NULL OR f.review IS NOT NULL) AS issue FROM files f", (CHECKED,))
    for row in rows:
        entry = found.setdefault(folder_of_stored(row["path"]), [0, 0, 0])
        entry[0] += 1
        entry[1] += row["done"]
        entry[2] += (not row["done"]) and row["issue"]
    return found


def parts_of(folder: str) -> list[str]:
    """The names a folder is made of; an absolute folder keeps its leading ``/`` on its first name."""
    names = [part for part in folder.split("/") if part]
    if folder.startswith("/") and names:
        names[0] = "/" + names[0]
    return names


def build(counts: dict[str, list[int]]) -> FolderNode:
    """The tree of folders, with the counts of every folder and of everything below it."""
    root = FolderNode("", "")
    for folder, (files, checked, *rest) in counts.items():
        issues = rest[0] if rest else 0
        node, here = root, []
        root.total += files
        root.below_checked += checked
        root.below_issues += issues
        for name in parts_of(folder):
            here.append(name)
            node = node.children.setdefault(name, FolderNode(name, "/".join(here).replace("//", "/")))
            node.total += files
            node.below_checked += checked
            node.below_issues += issues
        node.files += files
        node.checked += checked
        node.issues += issues
    return root


def find(root: FolderNode, folder: str) -> Optional[FolderNode]:
    node = root
    for name in parts_of(folder):
        node = node.children.get(name)
        if node is None:
            return None
    return node


def inconsistent(library) -> dict[str, str]:
    """Folders whose files say different things about themselves: more than one series name, or series and one-shots mixed.
    ``{folder: reason}``; only the folders that hold files directly (a parent of several series is not inconsistent)."""
    series: dict[str, set[str]] = {}
    kinds: dict[str, set[str]] = {}
    rows = library.conn.execute(
        "SELECT f.path, a.kind, s.name FROM files f JOIN albums a ON a.id = f.album_id LEFT JOIN series s ON s.id = a.series_id")
    for row in rows:
        folder = folder_of_stored(row["path"])
        kinds.setdefault(folder, set()).add(row["kind"])
        if row["name"]:
            series.setdefault(folder, set()).add(row["name"])
    found = {}
    for folder in sorted(set(series) | set(kinds)):
        if len(kinds.get(folder, ())) > 1:
            found[folder] = "series and one-shots mixed"
        elif len(series.get(folder, ())) > 1:
            found[folder] = f"{len(series[folder])} series names"
    return found


def stored_files(library, folder: str, recursive: bool = True) -> list[tuple[int, str, Optional[int]]]:
    """``(file id, stored path, album id)`` of the files of a folder (and below, when ``recursive``)."""
    prefix = folder.rstrip("/") + "/" if folder else ""
    rows = library.conn.execute("SELECT id, path, album_id FROM files ORDER BY path").fetchall()
    found = []
    for row in rows:
        path = row["path"]
        if folder and not path.startswith(prefix):
            continue
        if not recursive and "/" in path[len(prefix):]:
            continue
        found.append((row["id"], path, row["album_id"]))
    return found


def mark_reviewed(library, folder: str, recursive: bool = True) -> int:
    """Mark every file of the folder that is not reviewed yet as reviewed: a file with an album is kept (its flag goes), a file without
    is left as it is. Returns how many were marked. Nothing is moved, written or unlinked."""
    from bdnex.ui.review_cmd import settle

    marked = 0
    done = {row["file_id"] for row in library.conn.execute("SELECT file_id FROM file_attrs WHERE key = ?", (CHECKED,))}
    for file_id, stored, album_id in stored_files(library, folder, recursive):
        if file_id in done:
            continue
        if album_id is not None:
            settle(library, {"path": library._resolve(stored)}, "keep")
        library.set_attr("file", file_id, CHECKED, "keep" if album_id is not None else "leave")
        marked += 1
    return marked
