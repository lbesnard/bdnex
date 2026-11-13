"""Comparing two files that hold the same album, and suggesting which to keep.

An album can have several files: the same book as CBZ and CBR, a scan and a digital release... One is
the main file; the others are *spare versions* (placed in ``duplicates.versions_dir``). When a second
file for an album turns up, :func:`compare` lays the two side by side and says what they share, so the
user can choose with the facts in front of them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from bdnex.formatting import human_size

from bdnex.importer import covers
from bdnex.library.hashing import file_md5
from bdnex.library.scan import inspect_file

KEEP_EXISTING, KEEP_NEW = "keep_existing", "keep_new"
IDENTICAL, SAME_BOOK, PROBABLY_SAME, DIFFERENT = "identical", "same book", "probably the same book", "different"


@dataclass
class FileFacts:
    path: str
    format: str
    size: int
    pages: Optional[int]
    corrupted: bool
    md5: Optional[str]
    cover: Optional[int]  # 64-bit cover hash
    spare: bool = False

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def facts_of(library, path: str) -> FileFacts:
    """What is known of a file: from the library when it is registered, else read from the file."""
    row = library.get_file(path)
    if row is not None:
        return FileFacts(row["path"], row["format"], row["size"], row["page_count"], bool(row["corrupted"]),
                         row["md5"], covers.library_cover_hash(library, path), bool(row.get("spare")))
    info = inspect_file(path)
    return FileFacts(info.path, info.format, info.size, info.page_count, bool(info.corrupted), file_md5(info.path),
                     covers.path_cover_hash(info.path, info.format))


@dataclass
class Comparison:
    new: FileFacts
    existing: list[FileFacts]
    relation: str  # IDENTICAL | SAME_BOOK | PROBABLY_SAME | DIFFERENT
    suggestion: str  # KEEP_EXISTING | KEEP_NEW
    reason: str


def _rank(facts: FileFacts, prefer: list[str]) -> tuple:
    """Higher is better: a readable file, then more pages, then the preferred format."""
    order = prefer.index(facts.format) if facts.format in prefer else len(prefer)
    return (not facts.corrupted, facts.pages or 0, -order)


def _why(better: FileFacts, worse: FileFacts, prefer: list[str], worse_is: str) -> str:
    """Why ``better`` beats ``worse`` (``worse_is`` says which one that is: "existing" or "new")."""
    if better.corrupted != worse.corrupted:
        return f"the {worse_is} file is damaged"
    if (better.pages or 0) != (worse.pages or 0):
        return f"{better.pages} pages against {worse.pages}"
    if better.format != worse.format:
        return f"{better.format} is preferred to {worse.format} (duplicates.prefer)"
    return "nothing tells them apart"


def relation(a: FileFacts, b: FileFacts) -> str:
    if a.md5 and a.md5 == b.md5:
        return IDENTICAL
    if a.pages and a.pages == b.pages:
        if a.cover is not None and b.cover is not None:
            return SAME_BOOK if covers.hash_distance(a.cover, b.cover) <= covers.SAME_COVER_DISTANCE else DIFFERENT
        return PROBABLY_SAME
    return DIFFERENT


def compare(existing: list[FileFacts], new: FileFacts, prefer: list[str]) -> Comparison:
    """``new`` against the files already there. The suggestion keeps the existing best file unless the
    new one is better (never on a tie: the library is left as it is)."""
    best = max(existing, key=lambda f: _rank(f, prefer))
    kind = relation(best, new)
    if _rank(new, prefer) > _rank(best, prefer):
        return Comparison(new, existing, kind, KEEP_NEW, _why(new, best, prefer, "existing"))
    return Comparison(new, existing, kind, KEEP_EXISTING, _why(best, new, prefer, "new"))


def table(comparison: Comparison, root: Optional[str] = None) -> str:
    """The text shown when asking which version to keep: each file on two lines, then what they share."""
    def shown(facts: FileFacts) -> str:
        path = facts.path
        if root and path.startswith(root.rstrip("/") + "/"):
            path = path[len(root.rstrip("/")) + 1:]
        return path

    def details(facts: FileFacts) -> str:
        pages = f"{facts.pages} pages" if facts.pages is not None else "pages unknown"
        parts = [facts.format, pages, human_size(facts.size), "damaged" if facts.corrupted else "ok"]
        return " · ".join(parts + (["spare version"] if facts.spare else []))

    labels = [f"existing {i}" if len(comparison.existing) > 1 else "existing"
              for i in range(1, len(comparison.existing) + 1)] + ["new"]
    lines = []
    for label, facts in zip(labels, (*comparison.existing, comparison.new)):
        lines.append(f"  {label.ljust(10)}{shown(facts)}")
        lines.append(f"  {' ' * 10}{details(facts)}")
    lines.append("")
    lines.append(f"  {comparison.relation}: " + {IDENTICAL: "the two files have exactly the same bytes",
                                                SAME_BOOK: "same page count and the same cover",
                                                PROBABLY_SAME: "same page count (the covers could not be compared)",
                                                DIFFERENT: "different page count or cover"}[comparison.relation])
    lines.append(f"  suggested: {'keep the existing file' if comparison.suggestion == KEEP_EXISTING else 'keep the new file'}"
                 f" ({comparison.reason})")
    return "\n".join(lines)
