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
    shutil.move(str(source), str(destination))  # copies and removes across disks
    entry = library.add_trash(str(source), str(destination), reason,
                              album_id=row["album_id"] if row else None, md5=row["md5"] if row else None,
                              size=row["size"] if row else destination.stat().st_size,
                              fmt=row["format"] if row else destination.suffix.lstrip(".").lower())
    if row and forget:
        library.remove_file(str(source))
    return entry


def restore(library, entry_id: int) -> Optional[str]:
    """Put a file back where it came from (or beside it with a number if that name is taken) and
    register it again. Returns the new path, or ``None`` when the trashed file no longer exists."""
    entry = library.get_trash(entry_id)
    if entry is None:
        raise KeyError(f"no trash entry {entry_id}")
    trashed = Path(entry["trash_path"])
    if not trashed.exists():
        library.delete_trash(entry_id)
        return None
    target = unique(Path(entry["path"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(trashed), str(target))
    library.add_file(str(target), entry["album_id"])
    library.delete_trash(entry_id)
    _prune(trashed.parent)
    return str(target)


def empty(library, entries: list[dict[str, Any]]) -> tuple[int, int]:
    """Delete the trashed files and their records. Returns (files deleted, bytes freed)."""
    freed = count = 0
    for entry in entries:
        trashed = Path(entry["trash_path"])
        if trashed.exists():
            freed += trashed.stat().st_size
            trashed.unlink()
            count += 1
            _prune(trashed.parent)
        library.delete_trash(entry["id"])
    return count, freed


def _prune(folder: Path) -> None:
    """Remove the (dated) folder when nothing is left in it."""
    try:
        folder.rmdir()
    except OSError:
        pass
