"""``bdnex review --batch``: a table to check the matches a batch of files at a time (Textual).

One row per file: its name, its folder, the album it is linked to, the match score and the cover score. The keys decide
and every decision is saved at once (``bdnex.library.review_batch``), so the session can stop at any moment and be
continued later by running the command again.

Nothing slow runs in the screen's own thread: the files are weighed, the album list is loaded and searched, and a link
(which reads an album page) is made in other threads, so the table opens at once and says what it is doing. SQLite stays in
one thread each: the screen's library writes the decisions, a read-only one weighs the files, and the album list has a
thread (and library) of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import shutil
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from bdnex.library import review_batch as batch
from bdnex.ui import color

STATUS_STYLE = {"keep": "green", "linked": "green", "unlinked": "red", "left": "dim", "skipped": "yellow"}
KIND_STYLE = {"wrong": "red", "unconfirmed": "yellow", "unmatched": "magenta", "ok": "green"}
KIND_WORD = {"wrong": "contradicted", "unconfirmed": "unconfirmed", "unmatched": "no album", "ok": "nothing against"}
EVIDENCE_STYLE = {"-": "red", "+": "green", "?": "yellow", "f": "yellow"}
SEARCH_LIMIT = 60


def score_text(value: Optional[float]) -> Text:
    """A score in the colour of its value (red low, green high); ``-`` when there is none."""
    if value is None:
        return Text("-", style="dim")
    return Text(f"{value:.0f}%", style=color.score_hex(float(value)))


def album_text(row: batch.Row) -> Text:
    return Text(row.album or "(no album)", style="bold" if row.album else KIND_STYLE["unmatched"])


# ----------------------------------------------------------------------------- the album list, in a thread of its own
class SourceHub:
    """The metadata source and its album list, in one thread that owns them (SQLite connections stay in their thread).

    ``source`` is a source, or a function that builds one and returns it (or ``(source, close)``): it runs in that thread,
    so loading the album list never holds the screen."""

    def __init__(self, source):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bdnex-albums")
        self.source = None
        self.close_it = None
        self.catalogue: Optional[list] = None
        self.folded: list[str] = []
        build = source if callable(source) and not hasattr(source, "candidates") else (lambda: source)
        self.ready = self.pool.submit(self._build, build)

    def _build(self, build) -> None:
        made = build()
        self.source, self.close_it = made if isinstance(made, tuple) else (made, None)

    def run(self, function):
        """Run ``function(hub)`` in the thread of the album list (after it is built); returns a ``Future``."""
        return self.pool.submit(function, self)

    def close(self) -> None:
        if self.close_it is not None:
            with contextlib.suppress(Exception):
                self.pool.submit(self.close_it).result(timeout=5)
        self.pool.shutdown(wait=False, cancel_futures=True)


def fuzzy_search(hub: SourceHub, text: str, limit: int = SEARCH_LIMIT) -> tuple[list, int]:
    """The albums of the whole list that look like ``text``, best first, as ``([(candidate, score)], matches)``.

    Every word has to be in the name (case and accents do not matter); when that finds few, a fuzzy match over the whole
    list (letters missing, a typo) takes over: what ``fzf`` does, in the screen. Runs in the thread of the album list."""
    from rapidfuzz import fuzz, process

    from bdnex.library.query import fold
    from bdnex.ui.prompts import display_name

    if not hasattr(hub.source, "catalogue"):  # a source that cannot list its albums: its own search
        return [pair for pair in hub.source.candidates(text.strip(), limit=limit)], 0
    if hub.catalogue is None:
        hub.catalogue = list(hub.source.catalogue())
        hub.folded = [fold(display_name(candidate)) for candidate in hub.catalogue]
    query = fold(text).strip()
    if not query:
        return [], len(hub.catalogue)
    words = query.split()
    pool = [i for i, name in enumerate(hub.folded) if all(word in name for word in words)]
    if len(pool) > 4000:  # a letter or two: too many to rank, and typing more narrows it (the shorter names first)
        pool.sort(key=lambda i: len(hub.folded[i]))
        return [(hub.catalogue[i], 0.0) for i in pool[:limit]], len(pool)
    if pool:
        found = process.extract(query, {i: hub.folded[i] for i in pool}, scorer=fuzz.WRatio, limit=limit)
        return [(hub.catalogue[key], score) for _, score, key in found], len(pool)
    # no album has all the words: letters missing, a typo. A fuzzy match over the whole list, with a floor so that
    # unrelated names are not listed
    found = process.extract(query, hub.folded, scorer=fuzz.WRatio, limit=limit, score_cutoff=60)
    return [(hub.catalogue[index], score) for _, score, index in found], len(found)


class FuzzyScreen(ModalScreen):
    """Search the whole album list as you type (like ``fzf``): Enter picks, Ctrl-O opens the page, Esc goes back."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("ctrl+o", "open", "Open the page"),
                Binding("down", "move(1)", show=False, priority=True), Binding("up", "move(-1)", show=False, priority=True)]
    CSS = """
    FuzzyScreen { align: center middle; }
    #search { width: 90%; height: 85%; border: round $accent; padding: 1 2; background: $surface; }
    #hits { height: 1fr; }
    """

    def __init__(self, hub: SourceHub, start: str, title: str):
        super().__init__()
        self.hub, self.start, self.heading = hub, start, title
        self.hits: list = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search"):
            yield Label(Text(self.heading, style="bold"))
            yield Input(value=self.start, placeholder="type to find the album: words, in any order", id="words")
            yield Label("", id="count")
            yield OptionList(id="hits")
            yield Static("", id="preview")
            yield Label(Text("Enter picks the album · Ctrl-O opens its page · ↑↓ move · Ctrl-U clears · Esc goes back", style="dim"))

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self.search(self.start)

    def search(self, text: str) -> None:
        self.query_one("#count", Label).update(Text("looking...", style="dim"))
        self.run_worker(self.find(text), exclusive=True)  # a newer search replaces the one still running

    async def find(self, text: str) -> None:
        try:
            hits, total = await asyncio.wrap_future(self.hub.run(lambda hub: fuzzy_search(hub, text)))
        except Exception as err:  # the album list could not be loaded
            self.query_one("#count", Label).update(Text(f"the album list is not available: {err}", style="red"))
            return
        from bdnex.ui.prompts import display_name

        self.hits = hits
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options([Option(f"{score:>3.0f}  {display_name(candidate)}") for candidate, score in hits])
        if hits:
            options.highlighted = 0
        self.query_one("#count", Label).update(Text(f"{total} album(s) match" if text.strip() else "", style="dim"))

    def on_input_changed(self, event: Input.Changed) -> None:
        self.search(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.pick(self.query_one(OptionList).highlighted)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.pick(event.option_index)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_index is not None and 0 <= event.option_index < len(self.hits):
            self.query_one("#preview", Static).update(Text(self.hits[event.option_index][0].url, style="dim"))

    def action_move(self, step: int) -> None:
        options = self.query_one(OptionList)
        if self.hits:
            options.highlighted = max(0, min(len(self.hits) - 1, (options.highlighted or 0) + step))

    def pick(self, index: Optional[int]) -> None:
        if index is not None and 0 <= index < len(self.hits):
            self.dismiss(self.hits[index][0])

    def action_open(self) -> None:
        index = self.query_one(OptionList).highlighted
        if index is not None and 0 <= index < len(self.hits):
            webbrowser.open(self.hits[index][0].url)

    def action_back(self) -> None:
        self.dismiss(None)


class AskScreen(ModalScreen):
    """One line of text (an album address or number), or a yes/no question when ``text`` is ``None``."""

    BINDINGS = [Binding("escape", "back", "Back")]
    CSS = """
    AskScreen { align: center middle; }
    #ask { width: 80%; height: auto; border: round $accent; padding: 1 2; background: $surface; }
    """

    def __init__(self, question: str, text: Optional[str] = ""):
        super().__init__()
        self.question, self.text = question, text

    def compose(self) -> ComposeResult:
        with Vertical(id="ask"):
            yield Label(Text(self.question, style="bold"))
            if self.text is not None:
                yield Input(value=self.text, id="line")
            else:
                yield Label(Text("y: yes · n: no", style="dim"))

    def on_mount(self) -> None:
        if self.text is not None:
            self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_key(self, event) -> None:
        if self.text is None and event.key in ("y", "n"):
            self.dismiss(event.key == "y")

    def action_back(self) -> None:
        self.dismiss(None)


# ----------------------------------------------------------------------------- the table
class ReviewApp(App):
    """The table of one batch, the detail of the row under the cursor, and the keys that decide."""

    TITLE = "bdnex review"
    CSS = """
    #loading { height: 2fr; min-height: 8; content-align: center middle; text-align: left; }
    #table { height: 2fr; min-height: 8; }
    #detail { height: 1fr; min-height: 8; border-top: solid $accent; padding: 0 1; overflow-y: auto; }
    """
    BINDINGS = [
        Binding("k", "keep", "Keep"),
        Binding("u", "unlink", "Unlink"),
        Binding("o", "other", "Other album (search)"),
        Binding("O", "other_fzf", "Search with fzf", show=False),
        Binding("i", "ident", "Id / address"),
        Binding("s", "skip", "Skip"),
        Binding("l", "leave", "Leave"),
        Binding("p", "page", "Open page"),
        Binding("n", "next_batch", "Next batch"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, library, settings, source, size: int = 50, terms=(), unmatched: bool = False,
                 everything: bool = False, again: bool = False):
        super().__init__()
        self.library, self.settings = library, settings
        self.hub = SourceHub(source)
        self.batch_size, self.terms, self.unmatched = size, list(terms), unmatched
        self.everything, self.again = everything, again
        self.rows: list[batch.Row] = []
        self.number = 0
        self.decided: dict[str, int] = {}
        self.loading = False
        self.timing = ""
        self.started = 0.0  # when the files started to be weighed
        self.weighed_in: Optional[float] = None  # how long weighing took, once it did
        self.albums_in: Optional[float] = None  # how long the album list took to load, once it did

    # -- layout
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="loading")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for name, width in (("#", 3), ("File", 40), ("Folder", 22), ("Album now", 46), ("Match", 6), ("Cover", 6), ("State", 14)):
            table.add_column(name, width=width)  # long names are cut, so the scores and the state are always on screen
        self.started = time.monotonic()
        self.hub.ready.add_done_callback(self.list_loaded)
        self.load_batch()

    def on_unmount(self) -> None:
        self.hub.close()

    def list_loaded(self, future) -> None:  # called in the thread of the album list
        self.albums_in = time.monotonic() - self.started

    # -- the loading panel: what is being done, and for how long
    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def loading_text(self) -> Text:
        now = time.monotonic()
        frame = self.SPINNER[int(now * 10) % len(self.SPINNER)]
        weighing = self.weighed_in is None
        listing = not self.hub.ready.done()
        text = Text()
        text.append(f"{frame}  " if (weighing or listing) else "✓  ", style="bold cyan")
        text.append("Getting the batch ready\n\n", style="bold")
        text.append("   ")
        text.append(("✓ " if not weighing else f"{frame} "), style="green" if not weighing else "cyan")
        text.append("weighing the files: what each match says for and against, offline")
        text.append(f"   {self.weighed_in if not weighing else now - self.started:.1f} s\n", style="dim")
        text.append("   ")
        text.append(("✓ " if not listing else f"{frame} "), style="green" if not listing else "cyan")
        text.append("loading the album list (for the search)")
        text.append(f"   {self.albums_in if not listing and self.albums_in is not None else now - self.started:.1f} s\n", style="dim")
        text.append("\nThe table opens as soon as the files are weighed; the search waits for the album list.", style="dim")
        return text

    def show_loading(self, on: bool) -> None:
        self.query_one("#loading", Static).display = on
        self.query_one("#table", DataTable).display = not on
        if on:
            self.query_one("#loading", Static).update(self.loading_text())
            self.loading_timer = self.set_interval(0.1, lambda: self.query_one("#loading", Static).update(self.loading_text()))
        else:
            timer = getattr(self, "loading_timer", None)
            if timer is not None:
                timer.stop()

    # -- the batch: weighed in a thread, so the table is there at once
    def say(self, text: str, style: str = "") -> None:
        self.query_one("#detail", Static).update(Text(text, style=style))

    def load_batch(self) -> None:
        if self.loading:
            return
        self.loading = True
        self.number += 1
        self.rows = []
        self.query_one(DataTable).clear()
        self.sub_title = f"batch {self.number} · getting ready..."
        self.say("")
        self.started, self.weighed_in = time.monotonic(), None
        self.show_loading(True)
        self.run_worker(self.weigh, thread=True, exclusive=True, group="batch")

    def weigh(self) -> None:
        """In a thread: its own read-only library, the files weighed, the rows handed back to the screen."""
        from bdnex.library import Library

        started = time.monotonic()
        try:
            with Library.for_settings(self.settings, readonly=True) as library:
                rows = batch.next_batch(library, self.settings, self.batch_size, self.terms, self.unmatched,
                                        self.everything, self.again)
        except Exception as err:
            self.call_from_thread(self.show_error, str(err))
            return
        self.call_from_thread(self.show_batch, rows, time.monotonic() - started)

    def show_error(self, message: str) -> None:
        self.loading = False
        self.show_loading(False)
        self.sub_title = f"batch {self.number}"
        self.say(f"Could not weigh the files: {message}", "red")

    def show_batch(self, rows: list[batch.Row], seconds: float) -> None:
        self.loading = False
        self.weighed_in = seconds
        self.show_loading(False)
        self.query_one(DataTable).focus()
        self.rows = rows
        self.timing = f"weighed in {seconds:.1f} s"
        self.render_table()
        if not self.rows:
            self.sub_title = "nothing left to check"
            self.say("Nothing left to check: every doubtful file has been looked at. "
                     "`bdnex import --skipped --auto=all` decides the unlinked ones again.", "green")

    def render_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for number, row in enumerate(self.rows, start=1):
            table.add_row(*self.cells(number, row), key=str(number - 1))
        self.update_title()
        if self.rows:
            table.move_cursor(row=next((i for i, row in enumerate(self.rows) if not row.status), 0))
            self.show_detail(table.cursor_row)

    def update_title(self) -> None:
        done = sum(1 for row in self.rows if row.status)
        self.sub_title = f"batch {self.number} · {done}/{len(self.rows)} checked · {self.timing}".rstrip(" ·")

    def cells(self, number: int, row: batch.Row) -> list:
        import os

        if row.busy:
            state = Text("linking...", style="bold yellow")
        elif row.status:
            state = Text(row.status, style=STATUS_STYLE.get(row.status, ""))
        elif row.message.startswith(("refused", "failed")):
            state = Text(row.message.split(":")[0], style="bold red")
        else:  # what is wrong with it, until it is decided
            state = Text(KIND_WORD.get(row.kind, row.kind) + (f" (was {row.previous})" if row.previous else ""),
                         style=KIND_STYLE.get(row.kind, ""))
        match = Text("you", style="cyan") if row.status == "linked" else score_text(row.score)
        return [Text(str(number), style="cyan"), Text(os.path.basename(row.path), style="bold"),
                Text(row.folder, style="dim"), album_text(row), match, score_text(row.cover), state]

    def show_detail(self, index: int) -> None:
        if not (0 <= index < len(self.rows)):
            return
        row = self.rows[index]
        text = Text()
        text.append(row.path + "\n", style="dim")
        text.append(KIND_WORD.get(row.kind, row.kind), style=KIND_STYLE.get(row.kind, "")).append("   now: ").append(album_text(row))
        if row.album_url:
            text.append(f"   {row.album_url}", style="dim")
        text.append("\n")
        for line in row.why[:5]:
            text.append(f"  {line}\n", style=EVIDENCE_STYLE.get(line[:1], ""))
        if row.message:
            text.append(f"  {row.message}\n", style="bold yellow" if row.busy else "yellow")
        self.query_one("#detail", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.show_detail(event.cursor_row)

    # -- the current row
    def current(self) -> Optional[batch.Row]:
        table = self.query_one(DataTable)
        return self.rows[table.cursor_row] if self.rows and 0 <= table.cursor_row < len(self.rows) else None

    def idle(self) -> Optional[batch.Row]:
        """The row under the cursor, unless a link is still being made for it."""
        row = self.current()
        if row is not None and row.busy:
            self.notify("Still linking this file: wait for it to finish.", severity="warning")
            return None
        return row

    def refresh_row(self, row: batch.Row) -> None:
        table = self.query_one(DataTable)
        index = self.rows.index(row)
        for column, cell in enumerate(self.cells(index + 1, row)):
            table.update_cell_at((index, column), cell)
        self.update_title()
        if table.cursor_row == index:
            self.show_detail(index)

    def advance(self) -> None:
        """Move to the next row not decided yet; when none is left, offer the next batch."""
        table = self.query_one(DataTable)
        waiting = [i for i, row in enumerate(self.rows) if not row.status and not row.busy]
        if waiting:
            after = [i for i in waiting if i > table.cursor_row]
            table.move_cursor(row=(after or waiting)[0])
        elif self.rows and not any(row.busy for row in self.rows):
            self.push_screen(AskScreen(f"Batch {self.number} is done. Check the next batch?", None), self.continue_with)

    def continue_with(self, answer) -> None:
        if answer:
            self.load_batch()
        elif answer is False:
            self.exit()

    # -- decisions: each one is saved at once
    def record(self, action: str) -> None:
        row = self.idle()
        if row is None:
            return
        batch.decide(self.library, row, action)
        self.decided[row.status] = self.decided.get(row.status, 0) + 1
        self.refresh_row(row)
        self.advance()

    def action_keep(self) -> None:
        row = self.current()
        if row is not None and row.album_id is not None:
            self.record("keep")
        elif row is not None:
            self.notify("This file has no album to keep: `o` searches one, `l` leaves it.", severity="warning")

    def action_unlink(self) -> None:
        row = self.current()
        if row is not None and row.album_id is not None:
            self.record("unlink")
        elif row is not None:
            self.notify("This file has no album to unlink.", severity="warning")

    def action_leave(self) -> None:
        self.record("leave")

    def action_skip(self) -> None:
        row = self.idle()
        if row is not None:
            row.status = "skipped"  # nothing is saved: it is in the next batch again
            self.refresh_row(row)
            self.advance()

    def action_page(self) -> None:
        row = self.current()
        if row is not None and row.album_url:
            webbrowser.open(row.album_url)

    # -- another album
    def search_start(self, row: batch.Row) -> str:
        from bdnex.importer.matching import query_from_filename

        return query_from_filename(row.path)

    def action_other(self) -> None:
        """Another album: a search box over the whole album list, filtered as you type (the fuzzy search of ``fzf``)."""
        row = self.idle()
        if row is None:
            return
        self.push_screen(FuzzyScreen(self.hub, self.search_start(row), f"Which album is {row.path.rsplit('/', 1)[-1]}?"),
                         lambda candidate: self.link(row, candidate.url, candidate) if candidate is not None else None)

    def action_other_fzf(self) -> None:
        """The real ``fzf`` (when installed): the screen steps aside for it and comes back."""
        row = self.idle()
        if row is None:
            return
        source = self.hub.source
        if not (shutil.which("fzf") and hasattr(source, "catalogue")):
            self.notify("fzf is not installed (or the album list is not loaded yet): `o` is the search of the screen.",
                        severity="warning")
            return
        from textual.app import SuspendNotSupported

        from bdnex.library.query import fold
        from bdnex.ui.prompts import fzf_pick

        try:
            with self.suspend():
                candidate = fzf_pick(source, row.path, fold(self.search_start(row)))
        except SuspendNotSupported:
            self.notify("This terminal cannot hand over to fzf: use `o`.", severity="warning")
            return
        if candidate is not None:
            self.link(row, candidate.url, candidate)

    def action_ident(self) -> None:
        row = self.idle()
        if row is None:
            return

        async def given(text) -> None:
            if not text:
                return
            candidate = await asyncio.wrap_future(self.hub.run(lambda hub: hub.source.candidate_for(text)))
            if candidate is None:
                self.notify(f"{text!r} is not an album: give the address of its page, or its number.", severity="error")
                return
            self.link(row, candidate.url, candidate)

        self.push_screen(AskScreen("The album's page address, or its number:", ""),
                         lambda text: self.run_worker(given(text)) if text else None)

    # -- linking: it reads an album page, so it runs in a thread and the table says so
    def link(self, row: batch.Row, url: str, candidate=None) -> None:
        import os

        from bdnex.ui.prompts import display_name

        name = display_name(candidate) if candidate is not None else url
        before = (self.library.get_file(row.path) or {}).get("album_id")
        row.busy, row.message = True, f"linking to {name}: reading the album page, a few seconds..."
        self.refresh_row(row)
        self.notify(f"Linking {os.path.basename(row.path)} to {name}...", timeout=4)

        def work() -> None:
            from bdnex.ui import verdicts

            captured, error = io.StringIO(), ""
            try:  # the import prints its progress: it must not land on the screen
                with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                    verdicts.rematch(row.path, url)
            except Exception as err:
                error = str(err) or err.__class__.__name__
            self.call_from_thread(self.linked, row, before, error)

        self.run_worker(work, thread=True, exclusive=False, group="link")

    def linked(self, row: batch.Row, before, error: str = "") -> None:
        import os

        row.busy = False
        if error:
            row.message = f"failed: {error}"
            self.notify(f"{os.path.basename(row.path)}: {row.message}", severity="error")
            self.refresh_row(row)
            return
        ok, message = batch.finish_link(self.library, row, before)
        if ok:
            file = self.library.get_file(row.path) or {}
            row.cover, row.score, row.kind = file.get("cover_score"), None, "ok"
            row.why = ["+ chosen by you"]
            row.message = message + (f" · its cover is {row.cover:.0f}% like the file's" if row.cover is not None
                                     else " · cover not compared")
            self.decided["linked"] = self.decided.get("linked", 0) + 1
        self.notify(row.message, severity="information" if ok else "error", timeout=6)
        self.refresh_row(row)
        if ok:
            self.advance()

    def action_next_batch(self) -> None:
        self.load_batch()


def run(library, settings, source, size: int = 50, terms=(), unmatched: bool = False, everything: bool = False,
        again: bool = False) -> dict[str, int]:
    """Run the table until the person quits; returns how many files were decided, by decision."""
    app = ReviewApp(library, settings, source, size, terms, unmatched, everything, again)
    app.run()
    return app.decided
