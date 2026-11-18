"""The part of an import that looks at each file's cover and at whether its match makes sense.

After an album's page is read the album's cover is kept as a file, the file's own cover is compared with it
(``cover.check``), and the match is weighed against what the page says (:mod:`bdnex.importer.confirm`): a match that
looks wrong is flagged for ``bdnex review``, never unlinked on its own.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from bdnex.importer import confirm, covers
from bdnex.importer.session_types import SKIPPED, ImportResult, NeedsTerminal, album_label

logger = logging.getLogger(__name__)


class CoverPass:
    """Methods of :class:`~bdnex.importer.session.ImportSession` (a mixin: it keeps no state of its own)."""

    # ------------------------------------------------------------- covers
    def _check_covers(self) -> None:
        """Save and compare the covers of complete albums that were not part of this run's fetching."""
        waiting, self._covers = self._covers, []
        if waiting:
            self.progress(f"Getting {len(waiting)} cover(s) (one request each, politely)...")
        try:
            for result in waiting:
                self._check_cover(result)
        except KeyboardInterrupt:
            self.progress("Interrupted: the other covers are downloaded by the next `bdnex import --fetch`.")

    def _cover_missing(self, album: dict) -> bool:
        """Should this album's cover be downloaded (``cover.download``) and is it not there?"""
        if not self.settings.cover.download or not album.get("cover_url"):
            return False
        return self._kept_cover(album) is None

    def _kept_cover(self, album: dict) -> Optional[Path]:
        """The saved cover file of an album, if there is one. A file from an earlier library (the album was
        forgotten by `bdnex reset`, the folder was kept) is taken up again."""
        kept = album.get("cover_file")
        if kept and (self.settings.cover_path / kept).exists():
            return self.settings.cover_path / kept
        found = covers.find_cover(self.settings.cover_path, album["source"], album["source_id"])
        if found is not None and not self.pretend:
            self.library.set_cover_file(album["id"], found)
            album["cover_file"] = found
        return self.settings.cover_path / found if found else None

    def _cover_bytes(self, album: dict) -> Optional[bytes]:
        """The album's cover: the saved file, else downloaded now (and saved when ``cover.download``)."""
        kept = self._kept_cover(album)
        if kept is not None and not self.recache:
            data = kept.read_bytes()
            try:
                covers.check_cover_image(data)
                return data
            except covers.CoverError:  # a bad file left by an earlier run (an error page saved as a picture)
                if not self.pretend:
                    kept.unlink(missing_ok=True)
                    self.library.set_cover_file(album["id"], None)
                    album["cover_file"] = None
        fetch_image = getattr(self.source, "cover_image", None)
        if fetch_image is None or not album.get("cover_url"):
            return None
        data = fetch_image(album["cover_url"], recache=self.recache)
        covers.check_cover_image(data)  # never keep (or compare) what is not a picture
        if data and self.settings.cover.download and not self.pretend:
            relative = covers.save_cover(self.settings.cover_path, album["source"], album["source_id"], data)
            self.library.set_cover_file(album["id"], relative)
            album["cover_file"] = relative
        return data

    def _confirm(self, result: ImportResult) -> None:
        """Weigh the match with what the album page says (credits, year, volume, the file's own tags): a match that
        looks wrong is flagged for ``bdnex review`` with the reason, and left in place."""
        path = result.path
        album, row = (self.library.get_album(result.album_id) if result.album_id else None), self.library.get_file(path)
        if not album or row is None or self.pretend or album["extra"].get("partial") or row.get("review"):
            return
        try:  # (a partial album has no page to weigh against; a flag already there says why)
            embedded = row.get("embedded") or None
            evidence = confirm.judge(path, album, embedded)
        except (ValueError, TypeError, AttributeError) as err:
            logger.debug(f"{result.path}: match not weighed ({err})")
            return
        if confirm.worth_a_look(evidence):
            self.library.flag_review(path, confirm.review_reason(evidence))
            result.note = "; ".join(n for n in (result.note, f"the match looks {evidence.verdict}: `bdnex review` to look at it") if n)

    def _check_cover(self, result: ImportResult) -> None:
        """Keep the album's cover as a file; and, with ``cover.check``, compare the file's cover with it: below
        ``cover.match_percentage`` it is probably the wrong album. The score is kept with the file
        (``ls --files cover:..40``)."""
        path = result.path
        if result.album_id is None:
            return
        album, row = self.library.get_album(result.album_id), self.library.get_file(path)
        if not album or not album["cover_url"] or row is None:
            return
        try:
            online = self._cover_bytes(album) if (self.settings.cover.download or self.cover_check) else None
        except (covers.CoverError, *self.source.fetch_errors) as err:
            result.note = "; ".join(n for n in (result.note, f"cover not downloaded: {err}") if n)
            return
        if not self.cover_check or self._cover_off:
            return
        try:
            local = covers.front_cover(path, row["format"])
            if not local or not online:
                return
            score = round(covers.sift_similarity(local, online), 1)
        except covers.CoverUnavailable as err:
            self.progress(f"cover.check is on but {err}")
            self._cover_off = True
            return
        except (covers.CoverError, *self.source.fetch_errors) as err:
            result.note = "; ".join(n for n in (result.note, f"cover not compared: {err}") if n)
            return
        self.library.set_file_fields(path, cover_score=score)
        threshold = self.settings.cover.match_percentage
        if score >= threshold:
            return
        choice, decided = "keep", False
        asking = self.prompter is not None and self.auto != "all" and not self.pretend
        if asking and hasattr(self.prompter, "cover_mismatch"):
            try:
                choice = self.prompter.cover_mismatch(path, album_label(album), score, threshold)
                decided = True  # you looked at it: nothing is left to review
            except NeedsTerminal:
                choice = "keep"
        if choice == "unlink":
            self.library.set_file_album(path, None)
            self.library.log_import(path, SKIPPED, score)
            result.outcome, result.album_id = SKIPPED, None
            result.note = f"cover differs ({score:.0f}% < {threshold:.0f}%): unlinked from {album_label(album)}"
        else:
            if not decided and not self.pretend:
                self.library.flag_review(path, f"the cover differs from the album's ({score:.0f}% < {threshold:.0f}%)")
            result.note = "; ".join(n for n in (result.note, f"the cover differs from the album's ({score:.0f}% < {threshold:.0f}%)"
                                                + ("" if decided else ": `bdnex review` to look at it")) if n)
