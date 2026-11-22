"""The trash: files that were replaced wait in a folder, with a record in the library.

Nothing replaced is deleted. ``bdnex trash`` lists, restores and empties it; the library remembers
where each file came from and which album it belonged to.
"""

from __future__ import annotations

import datetime
import os
import shutil
from pathlib import Path
from typing import Any, Optional


class TrashError(OSError):
    """A trashed file can not be restored or deleted, and why: the record is kept."""


def _inside(root: Path, path: Path) -> bool:
    """Is ``path`` inside ``root`` (symbolic links resolved)? A trash record is only data: a path in it that leads elsewhere (a record from
    another library, a hand-edited one) must never make bdnex delete or move a file that is not in the trash."""
    try:
        Path(os.path.realpath(path)).relative_to(os.path.realpath(root))
        return True
    except ValueError:
        return False


def unique(path: Path) -> Path:
    """``path``, or ``path (2)``, ``path (3)``... when it exists."""
    if not path.exists():
        return path
    n = 2
    while path.with_name(f"{path.stem} ({n}){path.suffix}").exists():
        n += 1
    return path.with_name(f"{path.stem} ({n}){path.suffix}")


def move_to_trash(library, settings, path: str, reason: str, row: Optional[dict] = None,
                  forget: bool = True) -> int:
    """Move a file into the trash, forget it as a library file, and record it. Returns the trash id.

    ``row``: what the library knew of the file, when it is not (any more) under this path; ``forget=False``
    leaves the library's file record alone (it belongs to another path by now).
    """
    source = Path(os.path.abspath(path))
    row = row if row is not None else library.get_file(str(source))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = unique(settings.trash_path / stamp / source.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # recorded BEFORE the move: a crash or a locked database between the two used to leave a file in the trash that nothing knew about
    entry = library.add_trash(str(source), str(destination), reason,
                              album_id=row["album_id"] if row else None, md5=row["md5"] if row else None,
                              size=row["size"] if row else source.stat().st_size,
                              fmt=row["format"] if row else source.suffix.lstrip(".").lower())
    try:
        shutil.move(str(source), str(destination))  # copies and removes across disks
    except BaseException:
        library.delete_trash(entry)  # nothing was moved: no record of a file that is not there
        raise
    if row and forget:
        library.remove_file(str(source))
    return entry


def keep_copy(library, settings, path: str, reason: str, row: Optional[dict] = None) -> int:
    """Copy a file into the trash and record it, leaving the file where it is (it is about to be replaced in place).
    Returns the trash id; :func:`discard` takes the copy back out when the replacement did not happen."""
    source = Path(os.path.abspath(path))
    row = row if row is not None else library.get_file(str(source))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = unique(settings.trash_path / stamp / source.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(destination))
    return library.add_trash(str(source), str(destination), reason,
                             album_id=row["album_id"] if row else None, md5=row["md5"] if row else None,
                             size=row["size"] if row else destination.stat().st_size,
                             fmt=row["format"] if row else destination.suffix.lstrip(".").lower())


def discard(library, entry_id: int, settings=None) -> None:
    """Remove a copy made by :func:`keep_copy` (and its record)."""
    entry = library.get_trash(entry_id)
    if entry is None:
        return
    trashed = Path(entry["trash_path"])
    if settings is not None and not _inside(settings.trash_path, trashed):
        library.delete_trash(entry_id)  # the record points outside the trash: forget it, never delete what it names
        return
    if trashed.exists():
        trashed.unlink()
        _prune(trashed.parent)
    library.delete_trash(entry_id)


def restore(library, entry_id: int, settings=None) -> Optional[str]:
    """Put a file back where it came from (or beside it with a number if that name is taken) and
    register it again. Returns the new path, or ``None`` when the trashed file no longer exists.

    With ``settings``: a record that points outside the trash, or a place under a read-only (``copy``) source, is refused
    (:class:`TrashError`, the record is kept), and a trash folder that is not there (a disk not mounted) keeps its records."""
    entry = library.get_trash(entry_id)
    if entry is None:
        raise KeyError(f"no trash entry {entry_id}")
    trashed = Path(entry["trash_path"])
    if settings is not None:
        if not _inside(settings.trash_path, trashed):
            raise TrashError(f"{trashed} is not in the trash folder ({settings.trash_path}): the record is kept, the file is not touched")
        source = settings.source_for(entry["path"])
        if source is not None and source.mode == "copy":
            raise TrashError(f"{entry['path']} is in the read-only source {source.alias!r}: bdnex never writes there")
    if not trashed.exists():
        if settings is not None and not Path(settings.trash_path).exists():
            raise TrashError(f"the trash folder {settings.trash_path} is not there (a disk that is not mounted?): the record is kept")
        library.delete_trash(entry_id)
        return None
    target = unique(Path(entry["path"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(trashed), str(target))
    try:
        library.add_file(str(target), entry["album_id"])
    except BaseException:
        shutil.move(str(target), str(trashed))  # not registered: back in the trash, where its record still is
        raise
    library.delete_trash(entry_id)
    _prune(trashed.parent)
    return str(target)


def empty(library, entries: list[dict[str, Any]], settings=None, problems: Optional[list[str]] = None) -> tuple[int, int]:
    """Delete the trashed files and their records. Returns (files deleted, bytes freed).

    With ``settings``: a record that points outside the trash is only forgotten (what it names is never deleted), and when the trash
    folder is not there (a disk not mounted) nothing is forgotten. A file that can not be deleted keeps its record and is reported
    in ``problems`` instead of stopping the rest."""
    freed = count = 0
    problems = problems if problems is not None else []
    for entry in entries:
        trashed = Path(entry["trash_path"])
        if settings is not None and not _inside(settings.trash_path, trashed):
            problems.append(f"{trashed} is not in the trash folder: its record is dropped, the file is not touched")
            library.delete_trash(entry["id"])
            continue
        if not trashed.exists() and settings is not None and not Path(settings.trash_path).exists():
            problems.append(f"the trash folder {settings.trash_path} is not there (a disk not mounted?): {trashed.name} is kept in the list")
            continue
        try:
            if trashed.exists():
                size = trashed.stat().st_size
                trashed.unlink()
                freed += size
                count += 1
                _prune(trashed.parent)
        except OSError as err:
            problems.append(f"{trashed}: {err}")  # its record stays: it is still there
            continue
        library.delete_trash(entry["id"])
    return count, freed


def _prune(folder: Path) -> None:
    """Remove the (dated) folder when nothing is left in it."""
    try:
        folder.rmdir()
    except OSError:
        pass
