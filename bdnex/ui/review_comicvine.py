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

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from bdnex.importer import comicvine
from bdnex.library import review_batch as batch
from bdnex.ui.review_modal import Modal
from bdnex.ui.review_volumes import ChoiceScreen, VolumesScreen

logger = logging.getLogger(__name__)



class ComicvineSearchScreen(Modal):
    """Search Comic Vine for a volume (a series) by name. Enter searches (one request: not one per letter, the limit is 200 an hour), the arrows
    and Enter pick, and the answer is the volume's address, which ``a`` and ``i`` already know what to do with. Dismisses with ``None`` on Esc."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("ctrl+o", "open", "Open the page"),
                Binding("down", "move(1)", show=False, priority=True), Binding("up", "move(-1)", show=False, priority=True)]
    CSS = """
    ComicvineSearchScreen { align: center middle; }
    #cvsearch { width: 90%; height: 85%; border: round $accent; padding: 1 2; background: $surface; }
    #cvhits { height: 1fr; }
    #cvwords { border: tall $accent; background: $boost; }
    #cvwords:focus { border: double $success; background: $panel; }
    """

    def __init__(self, start: str = "", about=None) -> None:
        super().__init__()
        self.start, self.about = start, about or []
        self.hits: list = []
        self.searched = None  # the text the hits answer

    def compose(self) -> ComposeResult:
        with Vertical(id="cvsearch"):
            yield Label(Text("Search Comic Vine for the volume (the series) of the file(s)", style="bold"))
            if self.about:
                yield Label(Text("\n".join(self.about), style="cyan"))
            yield Input(value=self.start, id="cvwords", placeholder="the name of the series, then Enter")
            yield Label("", id="cvcount")
            yield OptionList(id="cvhits")
            yield Label(Text("Enter searches, then picks · ↑↓ move · Ctrl-O opens the page · Esc goes back. Each search is one request of the 200 an hour; "
                             "answers are kept.", style="dim"))

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def action_back(self) -> None:
        self.dismiss(None)

    def action_move(self, step: int) -> None:
        options = self.query_one(OptionList)
        if options.option_count:
            options.highlighted = max(0, min(options.option_count - 1, (options.highlighted or 0) + step))

    def action_open(self) -> None:
        index = self.query_one(OptionList).highlighted
        if index is not None and index < len(self.hits):
            from bdnex.ui.review_tui import open_in_browser, show_address

            if not open_in_browser(self.hits[index].url):
                show_address(self.app, self.hits[index].url)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if self.hits and text == self.searched:  # the same text again: Enter picks the highlighted volume
            self.pick(self.query_one(OptionList).highlighted)
        elif text:
            self.query_one("#cvcount", Label).update(Text("looking...", style="dim"))
            self.run_worker(self.find(text), exclusive=True)

    async def find(self, text: str) -> None:
        try:
            hits = await asyncio.to_thread(comicvine.search_volumes, self.app.settings, text)
        except Exception as err:
            if self.is_attached:
                self.query_one("#cvcount", Label).update(Text(str(err), style="red"))
            return
        if not self.is_attached:
            return
        self.hits, self.searched = hits, text
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options([Option(self.line(hit)) for hit in hits])
        if hits:
            options.highlighted = 0
        self.query_one("#cvcount", Label).update(Text(f"{len(hits)} volume(s)" if hits else "nothing found", style="dim"))

    @staticmethod
    def line(hit) -> Text:
        line = Text(f"{hit.year or '    ':<4}  ", style="dim")
        line.append(f"{(hit.publisher or '')[:18]:<18}  ", style="cyan")
        line.append(f"{hit.issues:>4} issue(s)  ", style="dim")
        line.append(hit.name, style="bold")
        return line

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.pick(event.option_index)

    def pick(self, index) -> None:
        if index is not None and index < len(self.hits):
            self.dismiss(self.hits[index].url)


class ReviewComicvine:
    """The mixin of the review app."""

    READING_CV = "reading Comic Vine"

    async def comicvine_address(self, chosen: list, text: str) -> None:
        """``i`` or ``a`` was given a Comic Vine address."""
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
            self.run_worker(self.guarded(self.comicvine_covers([(row, info)]), "the cover"))
            return
        paths = [r.path for r in chosen]
        self.comicvine_busy(chosen, True)
        try:
            answer = await asyncio.to_thread(comicvine.read_volume, self.settings, text, paths)  # ONE request: the volume and its issue list
        except Exception as err:
            self.notify(f"Could not read that page: {err}", severity="error", timeout=10)
            return
        finally:
            self.comicvine_busy(chosen, False)
        pairs = [(r, answer.matches[r.path]) for r in chosen if r.path in answer.matches]
        if not pairs and not getattr(answer.volume, "issues", None):
            self.notify(f"{answer.name}: Comic Vine lists no issue for this volume.", severity="warning", timeout=10)
            return
        left = len(chosen) - len(pairs)
        question = (f"{answer.name} (Comic Vine): {len(pairs)} of {len(chosen)} file(s) find their issue"
                    + (f" ({left} left as they are)" if left else "") + ". Check each issue; change one with Enter.")
        every = [(r, answer.matches.get(r.path)) for r in chosen]  # the files with no number are listed too: they can be matched by hand
        issues = sorted((e for e in answer.volume.issues if getattr(e, "id", None)), key=lambda e: (not str(e.number or "").isdigit(), int(e.number) if str(e.number or "").isdigit() else 0))
        options = [(self.issue_text(e), e) for e in issues]

        def pick(index: int, done) -> None:  # the issues of the volume are already here (the one request): no search, no cost
            self.push_screen(ChoiceScreen(f"Which issue of {answer.name}?", options), done)

        def make():
            return VolumesScreen(question, every, None, answer.name, describe=self.issue_text, pick=pick,
                                 note=lambda count: f"Linking reads {count} issue page(s): {count} of Comic Vine's 200 requests an hour (one was used to read the volume).")

        def chosen_issues(picked) -> None:
            if not picked:
                return
            for row, ref in picked:
                answer.matches[row.path] = ref  # a hand-picked issue is what the second step reads
            self.run_worker(self.guarded(self.comicvine_issues(picked, answer), "the issues"))

        self.ask_later(f"link {len(pairs)} file(s) to {answer.name}?", make, chosen_issues)

    @staticmethod
    def issue_text(ref) -> str:
        return f"{ref.name or 'issue'} (#{ref.number})"

    async def comicvine_issues(self, pairs: list, answer) -> None:
        """The matching was confirmed: the issue of each file is read (one request each), then the files are linked. A stop by the limit keeps the files
        that were read; the rest is read from the cache next time."""
        rows = [row for row, _ in pairs if not row.status and not row.busy]  # decided while the question was open
        paths = [row.path for row in rows]
        self.comicvine_busy(rows, True)
        self.notify(f"Reading {len(rows)} issue(s) of {answer.name}...", timeout=5)
        try:
            result = await asyncio.to_thread(comicvine.read_issues, self.settings, answer, paths)
        finally:
            self.comicvine_busy(rows, False)
        if result.stopped:
            self.notify(f"{result.stopped}. {len(result.infos)} of {len(rows)} issue(s) were read and are linked now; give the volume again later for the rest.",
                        severity="warning", timeout=12)
        self.link_infos([(row, result.infos[row.path]) for row in rows if row.path in result.infos])

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
        linked = []
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
            linked.append((row, info))
            done += 1
        self.notify(f"{done} file(s) linked to Comic Vine" + ("; their covers are being saved" if linked and self.settings.cover.download else ""), timeout=6)
        self.advance()
        if linked:
            self.run_worker(self.guarded(self.comicvine_covers(linked), "the covers"))

    async def comicvine_covers(self, linked: list) -> None:
        """Keep each linked album's cover as a file, so that ``c`` can put it next to the file's. One at a time: plain requests to Comic Vine's
        pictures (they are not the API: no limit counted), the file name decides, the library is written here, in the screen's thread."""
        for row, info in linked:
            kept = await asyncio.to_thread(comicvine.fetch_cover, self.settings, info)
            if kept and row.album_id is not None:
                self.library.set_cover_file(row.album_id, kept)
