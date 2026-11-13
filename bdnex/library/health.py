"""What `bdnex check` and `bdnex duplicates` look at: the consistency of the library and the files it points to."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bdnex.importer import covers
from bdnex.library.database import Library

TMP_SUFFIXES = (".bdnex-tmp", ".part")  # what an interrupted write or cover download leaves behind
SHOWN = 5  # examples listed per finding


@dataclass
class Finding:
    key: str
    problem: str                     # one line
    items: list[str] = field(default_factory=list)
    advice: str = ""
    fixable: bool = False            # `bdnex check --fix` can settle it


def check(library: Library, settings) -> list[Finding]:
    """Everything that looks wrong, most serious first. Reads the library and looks at the disk; changes nothing."""
    conn = library.conn
    found: list[Finding] = []

    integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        found.append(Finding("integrity", "the database file is damaged", integrity[:SHOWN],
                             "restore a copy (`<library>.bak-v<N>` or your own backup)"))
    broken = [f"{row[0]}: row {row[1]} points to a missing row of {row[2]}" for row in conn.execute("PRAGMA foreign_key_check")]
    if broken:
        found.append(Finding("references", f"{len(broken)} link(s) between tables point nowhere", broken[:SHOWN]))

    rows = [library._file_row(r) for r in conn.execute("SELECT * FROM files")]
    missing = [r["path"] for r in rows if not os.path.exists(r["path"])]
    if missing:
        found.append(Finding("missing", f"{len(missing)} file(s) are in the library but not on disk", missing[:SHOWN],
                             "`bdnex relink <folder>` finds them if they were only moved or renamed"))
    changed = [r["path"] for r in rows if r["path"] not in set(missing) and _changed(r)]
    if changed:
        found.append(Finding("changed", f"{len(changed)} file(s) changed on disk since they were recorded", changed[:SHOWN],
                             "`bdnex import --as-is --force <folder>` records them again"))
    unlinked = [r["path"] for r in rows if r["album_id"] is None]
    if unlinked:
        found.append(Finding("unmatched", f"{len(unlinked)} file(s) have no album", unlinked[:SHOWN],
                             "`bdnex import --select tagged:false` matches them"))
    waiting = [r["path"] for r in rows if r.get("review")]
    if waiting:
        found.append(Finding("review", f"{len(waiting)} match(es) wait for a review", waiting[:SHOWN], "`bdnex review`"))

    empty = [f"{row['title']}" for row in conn.execute(
        "SELECT title FROM albums WHERE id NOT IN (SELECT album_id FROM files WHERE album_id IS NOT NULL)")]
    if empty:
        found.append(Finding("albums-without-file", f"{len(empty)} album(s) have no file", empty[:SHOWN],
                             "normal after `bdnex import --fetch` of albums you removed; harmless"))
    series = [row["name"] for row in conn.execute(
        "SELECT name FROM series WHERE id NOT IN (SELECT series_id FROM albums WHERE series_id IS NOT NULL)")]
    if series:
        found.append(Finding("empty-series", f"{len(series)} series have no album", series[:SHOWN], fixable=True))

    gone_sources = [f"{alias}: {folder}" for alias, folder in library.roots.items() if not os.path.isdir(folder)]
    if gone_sources:
        found.append(Finding("source-missing", f"{len(gone_sources)} import source folder(s) do not exist", gone_sources[:SHOWN],
                             "the folder moved? `bdnex relocate OLD NEW`; or a disk that is not mounted"))

    covers_dir = settings.cover_path
    kept = {row["cover_file"] for row in conn.execute("SELECT cover_file FROM albums WHERE cover_file IS NOT NULL")}
    lost = sorted(name for name in kept if not (covers_dir / name).exists())
    if lost:
        found.append(Finding("cover-missing", f"{len(lost)} album(s) point to a cover file that is gone", lost[:SHOWN],
                             "`bdnex import --fetch` downloads them again", fixable=True))
    if covers_dir.is_dir():
        orphans = sorted(str(p.relative_to(covers_dir)) for p in covers_dir.rglob("*")
                         if p.is_file() and not p.name.endswith(TMP_SUFFIXES) and str(p.relative_to(covers_dir)) not in kept)
        if orphans:
            found.append(Finding("cover-orphans", f"{len(orphans)} cover file(s) belong to no album", orphans[:SHOWN],
                                 "kept on purpose after `bdnex reset` (they are reused); delete them by hand if unwanted"))

    flat = [str(p.relative_to(covers_dir)) for p in covers_dir.glob("*/*")
            if p.is_file() and p.suffix.lower() in covers.IMAGE_EXTENSIONS] if covers_dir.is_dir() else []
    if flat:
        found.append(Finding("cover-layout", f"{len(flat)} cover file(s) are in the old layout (one folder for all)",
                             flat[:SHOWN], "they are moved into a folder per album by the next import, or by `--fix`",
                             fixable=True))

    dead = [f"#{e['id']} {e['path']}" for e in library.trash_entries() if not Path(e["trash_path"]).exists()]
    if dead:
        found.append(Finding("trash-dead", f"{len(dead)} trash record(s) point to a file that is gone", dead[:SHOWN],
                             fixable=True))

    leftovers = _leftovers(settings, library)
    if leftovers:
        found.append(Finding("leftovers", f"{len(leftovers)} temporary file(s) left by an interrupted write", leftovers[:SHOWN],
                             "safe to delete", fixable=True))
    return found


def _changed(row: dict[str, Any]) -> bool:
    try:
        stat = os.stat(row["path"])
    except OSError:
        return False
    return stat.st_size != row["size"]


def _leftovers(settings, library: Library) -> list[str]:
    roots = {settings.cover_path}
    if library.root:
        roots.add(Path(library.root))
    found: list[str] = []
    for root in roots:
        if root.is_dir():
            found.extend(str(p) for p in root.rglob("*") if p.is_file() and p.name.endswith(TMP_SUFFIXES)
                         and not any(part.startswith(".") for part in p.relative_to(root).parts[:-1]))
    return sorted(found)


def fix(library: Library, settings, findings: list[Finding]) -> list[str]:
    """Settle the findings that are safe to settle. Returns what was done, in words."""
    done = []
    keys = {f.key for f in findings if f.fixable}
    if "empty-series" in keys:
        done.append(f"removed {library.prune_orphan_series()} empty series")
    if "cover-layout" in keys:
        done.append(f"moved {covers.migrate_flat_covers(library, settings.cover_path)} cover file(s) into a folder per album")
    if "cover-missing" in keys:
        with library.conn:
            count = library.conn.execute("SELECT cover_file FROM albums WHERE cover_file IS NOT NULL").fetchall()
            gone = [r[0] for r in count if not (settings.cover_path / r[0]).exists()]
            for name in gone:
                library.conn.execute("UPDATE albums SET cover_file = NULL WHERE cover_file = ?", (name,))
        done.append(f"forgot {len(gone)} cover file(s) that are gone (the next `import --fetch` downloads them)")
    if "trash-dead" in keys:
        dead = [e for e in library.trash_entries() if not Path(e["trash_path"]).exists()]
        for entry in dead:
            library.delete_trash(entry["id"])
        done.append(f"removed {len(dead)} dead trash record(s)")
    if "leftovers" in keys:
        files = _leftovers(settings, library)
        for path in files:
            os.remove(path)
        done.append(f"deleted {len(files)} temporary file(s)")
    return done


# --------------------------------------------------------------------------- duplicates
def duplicates(library: Library, compute_covers: bool = False, progress=None, workers: int = 1) -> list[dict[str, Any]]:
    """Groups of files that look like the same book: ``{"why": ..., "files": [rows]}``.

    * ``identical``: the same bytes (checksum) at several paths;
    * ``album``: several files linked to one album that are not marked as spare versions;
    * ``cover``: the first pages look alike (perceptual hash). Only the hashes the library already knows are used,
      unless ``compute_covers``: then the missing ones are worked out (the first page of every file is read).
    """
    rows = [library._file_row(r) for r in library.conn.execute("SELECT * FROM files")]
    if compute_covers:
        missing = [row for row in rows if not row.get("cover_hash") and os.path.exists(row["path"])]
        from bdnex.parallel import map_ordered

        # the first page of each file is read in several processes at once; the library is written here, in order
        hashes = map_ordered(lambda row: covers.path_cover_hash(row["path"], row["format"]), missing, workers)
        for number, (row, value) in enumerate(hashes, start=1):
            if progress:
                progress(f"[{number}/{len(missing)}] {os.path.basename(row['path'])}")
            if isinstance(value, Exception) or value is None:
                continue
            library.set_file_fields(row["path"], cover_hash=covers.format_hash(value))
            row["cover_hash"] = covers.format_hash(value)
    groups: list[dict[str, Any]] = []
    seen: set[frozenset] = set()

    def add(why: str, files: list[dict[str, Any]]) -> None:
        key = frozenset(f["path"] for f in files)
        if len(files) > 1 and not any(key <= done for done in seen):
            seen.add(key)
            groups.append({"why": why, "files": sorted(files, key=lambda f: f["path"])})

    by_md5: dict[str, list] = {}
    for row in rows:
        by_md5.setdefault(row["md5"], []).append(row)
    for files in by_md5.values():
        add("identical content", files)

    by_album: dict[int, list] = {}
    for row in rows:
        if row["album_id"] is not None and not row["spare"]:
            by_album.setdefault(row["album_id"], []).append(row)
    for files in by_album.values():
        add("same album", files)

    hashed = [(covers.parse_hash(row["cover_hash"]), row) for row in rows if row.get("cover_hash")]
    used: set[str] = set()
    for i, (hash_a, row_a) in enumerate(hashed):
        if row_a["path"] in used:
            continue
        near = [row_a] + [row_b for hash_b, row_b in hashed[i + 1:]
                          if row_b["path"] not in used and covers.hash_distance(hash_a, hash_b) <= covers.SAME_COVER_DISTANCE]
        if len(near) > 1:
            used.update(f["path"] for f in near)
            add("similar cover", near)
    return groups
