"""Comic Vine in the review screen: a pasted address (``i``, or ``a`` for a volume) links files to what the page says.

An issue address is one album for the file under the cursor. A volume address is a series: each marked file gets the issue with the number
in its name, after a question that lists them. Reading is slow (the API, one request a second at best, 200 an hour): it runs in a thread, the
rows say so in their state column, and a stop by the limit keeps the files that were done.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from bdnex.importer import comicvine
from bdnex.library import review_batch as batch

logger = logging.getLogger(__name__)

SHOWN = 8


class ReviewComicvine:
    """The mixin of the review app."""

    READING_CV = "reading Comic Vine"

    async def comicvine_address(self, chosen: list, text: str) -> None:
        """``i`` or ``a`` was given a Comic Vine address."""
        from bdnex.ui.review_tui import AskScreen  # (a circular import at the top)

        kind = comicvine.parse(text)[0]
        text = text.strip()
        if kind == "issue":
            if len(chosen) != 1:
                self.notify("An issue is one album: mark one file, or give the address of the volume (…/4050-NNNN/) to link several.",
                            severity="warning", timeout=8)
                return
            row = chosen[0]
            self.comicvine_busy([row], True)
            try:
                info = await asyncio.to_thread(comicvine.read_issue, self.settings, text)
            except Exception as err:
                self.notify(f"Could not read that page: {err}", severity="error", timeout=10)
                return
            finally:
                self.comicvine_busy([row], False)
            self.link_page(row, info)
            return
        paths = [r.path for r in chosen]
        self.comicvine_busy(chosen, True)
        try:
            answer = await asyncio.to_thread(comicvine.read_volume, self.settings, text, paths)
        except Exception as err:
            self.notify(f"Could not read that page: {err}", severity="error", timeout=10)
            return
        finally:
            self.comicvine_busy(chosen, False)
        pairs = [(r, answer.mapping[r.path]) for r in chosen if r.path in answer.mapping]
        if answer.stopped:
            self.notify(f"{answer.stopped}. {len(pairs)} file(s) were read; link them, then give the address again for the rest.", severity="warning", timeout=12)
        if not pairs:
            self.notify(f"{answer.name}: no file has a volume number the series has (T03, Tome 3, #3...).", severity="warning", timeout=10)
            return
        lines = [f"{os.path.basename(r.path)}  ->  {info.title}" + (f" (#{info.volume})" if info.volume is not None else "") for r, info in pairs[:SHOWN]]
        more = f"\n... and {len(pairs) - SHOWN} more" if len(pairs) > SHOWN else ""
        left = len(chosen) - len(pairs)
        question = (f"{answer.name} (Comic Vine): {len(pairs)} of {len(chosen)} file(s) find their issue"
                    + (f" ({left} left as they are)" if left else "") + ". Link them?\n" + "\n".join(lines) + more)
        self.ask_later(f"link {len(pairs)} file(s) to {answer.name}?", lambda: AskScreen(question, None),
                       lambda yes: self.link_infos(pairs) if yes else None)

    def comicvine_busy(self, rows: list, on: bool) -> None:
        now = time.monotonic()
        for row in rows:
            if on and not row.busy:
                row.busy, row.busy_what, row.started_at = True, self.READING_CV, now
            elif not on and row.busy_what == self.READING_CV:
                row.busy, row.busy_what, row.started_at = False, "", 0.0
            else:
                continue
            if row in self.rows:
                self.refresh_row(row)

    def link_infos(self, pairs: list) -> None:
        """Each file linked to its album now (a write in the library: the album's record is already complete, nothing is fetched)."""
        done = 0
        for row, info in pairs:
            if row.status or row.busy:  # decided while the question was open
                continue
            self.supersede(row)
            try:
                batch.link_info(self.library, row, info)
            except Exception as err:
                self.link_failed(row, err)
                continue
            self.refresh_dest(row)
            self.marked.discard(id(row))
            self.decided["linked"] = self.decided.get("linked", 0) + 1
            logger.info("%s: %s", os.path.basename(row.path), row.message)
            self.refresh_row(row)
            done += 1
        self.notify(f"{done} file(s) linked to Comic Vine", timeout=6)
        self.advance()
