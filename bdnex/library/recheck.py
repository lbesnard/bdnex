"""The judgement behind ``bdnex review --auto``: the matches that were applied below ``match.strong_threshold``, weighed again.

Nothing here asks the site or reads a page: it uses what the library already knows (the album's page, the file's own
tags and, when OpenCV is installed, the file's cover against the album's saved cover).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from bdnex.importer import confirm, covers
from bdnex.library import Library

logger = logging.getLogger(__name__)

KEPT, WRONG, UNCONFIRMED = "kept", "wrong", "unconfirmed"


@dataclass
class Verdict:
    path: str
    album: str
    album_id: int
    score: Optional[float]  # the score the match was applied at (``None``: not logged)
    kind: str = UNCONFIRMED
    cover: Optional[float] = None
    better: Optional[dict] = None  # another album whose cover is clearly more like the file's: ``{url, album, score, cover}``
    for_: list[str] = field(default_factory=list)
    against: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "album": self.album, "score": self.score, "verdict": self.kind, "cover": self.cover,
                "for": self.for_, "against": self.against, "better": self.better}


def _cover_against_saved(library: Library, settings, path: str, album: dict, row: dict) -> Optional[float]:
    """The similarity of the file's cover with the album's *saved* cover (no request), or ``None`` when it can not be told."""
    if not covers.opencv_installed():
        return None
    kept = album.get("cover_file")
    saved = (settings.cover_path / kept) if kept else None
    if saved is None or not saved.exists():
        found = covers.find_cover(settings.cover_path, album["source"], album["source_id"])
        saved = (settings.cover_path / found) if found else None
    if saved is None or not saved.exists():
        return None
    try:
        local = covers.front_cover(path, row.get("format") or os.path.splitext(path)[1].lstrip(".").lower())
        online = saved.read_bytes()
        covers.check_cover_image(online)
        return round(covers.sift_similarity(local, online), 1) if local else None
    except (covers.CoverError, covers.CoverUnavailable, OSError) as err:
        logger.debug(f"{path}: cover not compared ({err})")
        return None


def weak_matches(library: Library, settings, terms: list[str], compare: bool = True, store: bool = True,
                 flagged: bool = False, weak: bool = True) -> list[Verdict]:
    """Each linked file whose match was applied under ``match.strong_threshold`` (``weak``) or is flagged for review
    (``flagged``, whatever its score), with what confirms or contradicts it. A file you chose yourself (no score in the
    import log) is never looked at.

    ``compare`` computes the cover score that is missing (from the saved cover, no request) and ``store`` keeps it."""
    strong = settings.match.strong_threshold
    vote_min, cover_floor = settings.match.cover_vote_min, settings.cover.match_percentage
    albums: dict[int, dict] = {}
    found = []
    for row in library.query([*(["review:true"] if flagged and not weak else []), *terms], files=True):
        if row.get("id") is None:
            continue
        last = library.last_import(row["path"])
        if last and last["score"] is None:
            continue  # chosen by you or read from its own tags
        under = bool(last) and last["score"] < strong
        if not ((weak and under) or (flagged and row.get("review"))):
            continue  # confident, or not flagged: nothing to look at
        file = library.get_file(row["path"]) or row
        album = albums.get(row["id"]) or albums.setdefault(row["id"], library.get_album(row["id"]))
        if album is None:
            continue
        verdict = Verdict(row["path"], f"{album.get('series') or ''} {album['title']}".strip(), row["id"], last["score"] if last else None)
        if not album["extra"].get("partial"):
            ev = confirm.assess(row["path"], album, file.get("embedded") or None)
            verdict.against += ev.strong
            if ev.verdict == confirm.CONFIRMED:
                verdict.for_ += [a for a in ev.agree if not a.startswith("tome")]
        cover = file.get("cover_score")
        if cover is None and compare:
            cover = _cover_against_saved(library, settings, row["path"], album, file)
            if cover is not None and store:
                library.set_file_fields(row["path"], cover_score=cover)
        verdict.cover = cover
        if cover is not None:
            (verdict.for_ if cover >= vote_min else verdict.against if cover < cover_floor else []).append(
                f"its cover is {cover:.0f}% like the album's")
        if verdict.for_ and not verdict.against:
            verdict.kind = KEPT
        elif verdict.against and not verdict.for_:
            verdict.kind = WRONG
        else:
            verdict.kind = UNCONFIRMED  # nothing confirms it, or the evidence disagrees with itself
        found.append(verdict)
    return found
