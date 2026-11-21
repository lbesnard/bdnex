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
import logging
import os
import re
import shutil
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, OptionList, RichLog, Static, TextArea
from textual.widgets.option_list import Option

from bdnex.library import review_batch as batch
from bdnex.ui import color

STATUS_STYLE = {"keep": "green", "linked": "green", "unlinked": "red", "left": "dim", "skipped": "yellow"}
KIND_STYLE = {"wrong": "red", "unconfirmed": "yellow", "unmatched": "magenta", "ok": "green"}
KIND_WORD = {"wrong": "contradicted", "unconfirmed": "unconfirmed", "unmatched": "no album", "ok": "nothing against"}
EVIDENCE_STYLE = {"-": "red", "+": "green", "?": "yellow", "f": "yellow"}
SEARCH_LIMIT = 60
COLUMNS = (("#", 3), ("File", 40), ("Folder", 22), ("Album now", 40), ("Page", 8), ("Match", 8), ("Cover", 8), ("State", 14))
STATE_ORDER = {"wrong": 0, "unconfirmed": 1, "unmatched": 2, "ok": 3}  # the state column sorts by how doubtful, then by decision
DECIDED_ORDER = {"keep": 10, "linked": 11, "unlinked": 12, "left": 13, "skipped": 14}


def score_text(value: Optional[float]) -> Text:
    """A score in the colour of its value (red low, green high); ``-`` when there is none."""
    if value is None:
        return Text("-", style="dim")
    return Text(f"{value:.0f}%", style=color.score_hex(float(value)))


def page_number(url: Optional[str]) -> str:
    import re

    found = re.search(r"-(\d+)\.html", url or "")
    return found.group(1) if found else ""


def page_text(url: Optional[str]) -> Text:
    """The album's number on the site, as a link: a terminal that knows links (most do) opens it on a click, even over SSH."""
    number = page_number(url)
    return Text(number, style=Style(color="cyan", underline=True, link=url)) if number else Text("-", style="dim")


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


logger = logging.getLogger("bdnex.review")


class LogPanel(logging.Handler):
    """bdnex's log lines (any thread) into the panel at the bottom of the screen."""

    def __init__(self, app, level):
        super().__init__(level)
        self.app = app
        self.setFormatter(logging.Formatter("%(levelname).1s %(name)s: %(message)s"))

    def emit(self, record) -> None:
        try:
            self.app.log_from_anywhere(self.format(record))
        except Exception:  # a log line is never a reason to fail
            pass


class LineSink(io.StringIO):
    """What an import prints while it runs in a thread: each line goes to the panel (and is kept, for the error text)."""

    def __init__(self, app):
        super().__init__()
        self.app, self.pending = app, ""

    def write(self, text: str) -> int:
        self.pending += text.replace("\r", "\n")
        *lines, self.pending = self.pending.split("\n")
        for line in lines:
            if line.strip():
                self.app.log_from_anywhere(line.rstrip())
        return super().write(text)


SERIES_ADDRESS = re.compile(r"bedetheque\.com/serie-\d+", re.I)


def is_series_address(text: str) -> bool:
    """The address of a series page, not of an album: it is not in the album list (that lists albums), the series page has them."""
    return bool(SERIES_ADDRESS.search(text or ""))


def fuzzy_search(hub: SourceHub, text: str, limit: int = SEARCH_LIMIT) -> tuple[list, int]:
    """The albums of the whole list that look like ``text``, best first, as ``([(candidate, score)], matches)``.

    Every word has to be in the name (case and accents do not matter); when that finds few, a fuzzy match over the whole
    list (letters missing, a typo) takes over: what ``fzf`` does, in the screen. Runs in the thread of the album list."""
    from bdnex.importer import album_search

    if not hasattr(hub.source, "catalogue"):  # a source that cannot list its albums: its own search
        return [pair for pair in hub.source.candidates(text.strip(), limit=limit)], 0
    if hub.catalogue is None:
        hub.catalogue = list(hub.source.catalogue())
        hub.folded = album_search.folded_names(hub.catalogue)
    return album_search.search_folded(hub.catalogue, hub.folded, text, limit)


class FuzzyScreen(ModalScreen):
    """Search the whole album list as you type (like ``fzf``): Enter picks, Ctrl-O opens the page, Esc goes back."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("ctrl+o", "open", "Open the page"),
                Binding("down", "move(1)", show=False, priority=True), Binding("up", "move(-1)", show=False, priority=True)]
    CSS = """
    FuzzyScreen { align: center middle; }
    #search { width: 90%; height: 85%; border: round $accent; padding: 1 2; background: $surface; }
    #hits { height: 1fr; }
    """

    def __init__(self, hub: SourceHub, start: str, title: str, series_ok: bool = False):
        super().__init__()
        self.hub, self.start, self.heading, self.series_ok = hub, start, title, series_ok
        self.hits: list = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search"):
            yield Label(Text(self.heading, style="bold"))
            yield Input(value=self.start, id="words", placeholder=(
                "words of the album, in any order, or the address of the series page" if self.series_ok else "type to find the album: words, in any order"))
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
        if self.series_ok and is_series_address(event.value):
            self.dismiss(event.value.strip())  # the address of a series page: its albums are read from that page
            return
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
            event.stop()  # `n` is also the screen's "next batch"
            self.dismiss(event.key == "y")

    def action_back(self) -> None:
        self.dismiss(None)


class EditScreen(ModalScreen):
    """A form for an album written by hand (one file), or for the series of several files: Ctrl-S accepts, Esc cancels.

    ``volumes``: for several files, ``[(path, volume or None)]``: one line each, ``volume  file name``, the numbers can be changed."""

    BINDINGS = [Binding("escape", "back", "Cancel"), Binding("ctrl+s", "accept", "Save")]
    CSS = """
    EditScreen { align: center middle; }
    #edit { width: 90%; height: auto; max-height: 95%; border: round $accent; padding: 1 2; background: $surface; overflow-y: auto; }
    #edit Input { margin-bottom: 0; }
    #volumes { height: 8; }
    #problem { color: $error; }
    """

    def __init__(self, heading: str, values: dict, fields, volumes=None):
        super().__init__()
        self.heading, self.values, self.fields, self.volumes = heading, values, fields, volumes

    def compose(self) -> ComposeResult:
        with Vertical(id="edit"):
            yield Label(Text(self.heading, style="bold"))
            for key, label, hint in self.fields:
                yield Label(Text(f"{label}" + (f"  ({hint})" if hint else ""), style="dim"))
                yield Input(value=self.values.get(key, ""), id=f"f-{key}")
            if self.volumes is not None:
                yield Label(Text("Volume and file (change a number if it was read wrong)", style="dim"))
                yield TextArea("\n".join(f"{'' if v is None else v}\t{os.path.basename(p)}" for p, v in self.volumes), id="volumes")
            yield Label("", id="problem")
            yield Label(Text("Tab moves · Ctrl-S saves and links · Esc cancels", style="dim"))

    def on_mount(self) -> None:
        self.query_one(f"#f-{self.fields[0][0]}", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.screen.focus_next()

    def action_accept(self) -> None:
        values = {key: self.query_one(f"#f-{key}", Input).value.strip() for key, _, _ in self.fields}
        if self.volumes is not None:
            lines = self.query_one("#volumes", TextArea).text.split("\n")
            numbers = []
            for line in lines:
                first = line.split("\t", 1)[0].strip()
                if first and not first.isdigit():
                    self.query_one("#problem", Label).update(f"volume must be a number: {first!r}")
                    return
                numbers.append(int(first) if first else None)
            if len(numbers) != len(self.volumes):
                self.query_one("#problem", Label).update("keep one line per file")
                return
            values["__volumes__"] = numbers
        self.dismiss(values)

    def action_back(self) -> None:
        self.dismiss(None)


class CoverScreen(ModalScreen):
    """The file's first page next to the album's saved cover, drawn with text (works over SSH). Any key closes it."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("c", "close", "Close", show=False),
                Binding("q", "close", "Close", show=False)]
    CSS = """
    CoverScreen { align: center middle; }
    #covers { width: 90%; height: auto; max-height: 95%; border: round $accent; padding: 1 2; background: $surface; }
    #pair { height: auto; }
    #pair Static { width: 1fr; height: auto; }
    """

    def __init__(self, title: str, files) -> None:
        super().__init__()
        self.title_text, self.files = title, files

    def compose(self) -> ComposeResult:
        with Vertical(id="covers"):
            yield Label(Text(self.title_text, style="bold"))
            with Horizontal(id="pair"):
                yield Static(Text("reading the file...", style="dim"), id="left")
                yield Static(Text(""), id="right")
            yield Label(Text("c / Esc: close", style="dim"), id="foot")

    def on_mount(self) -> None:
        self.run_worker(self.load, thread=True, exclusive=True)

    def load(self) -> None:
        from bdnex.ui.cover_text import half_blocks

        local, online, notes = batch.read_covers(*self.files)
        width = max(20, min(40, (self.app.size.width * 9 // 10 - 12) // 2))
        left = half_blocks(local, width) if local else None
        right = half_blocks(online, width) if online else None
        self.app.call_from_thread(self.show_pair, left, right, notes)

    def show_pair(self, left, right, notes: str) -> None:
        def side(heading: str, picture) -> Text:
            text = Text(heading + "\n", style="bold")
            text.append_text(picture if picture is not None else Text("(none)", style="dim"))
            return text

        self.query_one("#left", Static).update(side("the file", left))
        self.query_one("#right", Static).update(side("the album (saved cover)", right))
        if notes:
            self.query_one("#foot", Label).update(Text(notes + "   ·   c / Esc: close", style="yellow"))

    def action_close(self) -> None:
        self.dismiss(None)


# ----------------------------------------------------------------------------- the table
class ReviewApp(App):
    """The table of one batch, the detail of the row under the cursor, and the keys that decide."""

    TITLE = "bdnex review"
    CSS = """
    #loading { height: 2fr; min-height: 8; content-align: center middle; text-align: left; }
    #table { height: 2fr; min-height: 8; }
    #detail { height: 1fr; min-height: 8; border-top: solid $accent; padding: 0 1; overflow-y: auto; }
    #log { height: 7; border-top: solid $accent; padding: 0 1; color: $text-muted; }
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
        Binding("c", "cover", "Covers"),
        Binding("space", "mark", "Mark"),
        Binding("ctrl+a", "mark_all", "Mark all / none"),
        Binding("x", "mark_none", "Unmark all"),
        Binding("v", "toggle_log", "Log"),
        Binding("a", "attach", "Attach marked to a series"),
        Binding("e", "edit", "Edit by hand"),
        Binding("n", "next_batch", "Next batch"),
        Binding("q", "quit", "Quit"),
        *[Binding(str(number), f"sort({number - 1})", f"Sort by {name}", show=False) for number, (name, _) in enumerate(COLUMNS, start=1)],
    ]

    def __init__(self, library, settings, source, size: int = 50, terms=(), unmatched: bool = False,
                 everything: bool = False, again: bool = False, verbose: int = 0):
        super().__init__()
        self.verbose = verbose
        self.ui_thread = None
        self.library, self.settings = library, settings
        self.hub = SourceHub(source)
        self.batch_size, self.terms, self.unmatched = size, list(terms), unmatched
        self.everything, self.again = everything, again
        self.rows: list[batch.Row] = []
        self.shown: set[str] = set()  # every file of every batch of this run: the next batch never repeats one
        self.number = 0
        self.decided: dict[str, int] = {}
        self.loading = False
        self.timing = ""
        self.sort_column: Optional[int] = None  # a column of COLUMNS, sorted by a click on its header or by a number key
        self.sort_reverse = False
        self.marked: set[int] = set()  # ids of the rows marked with space (to attach to a series together)
        self.order: dict[int, int] = {}  # each row's place when the batch was made (``#`` sorts back to it)
        self.started = 0.0  # when the files started to be weighed
        self.weighed_in: Optional[float] = None  # how long weighing took, once it did
        self.albums_in: Optional[float] = None  # how long the album list took to load, once it did

    # -- layout
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="loading")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="detail")
        yield RichLog(id="log", max_lines=500, wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        import threading

        self.ui_thread = threading.get_ident()
        self.started = time.monotonic()
        self.hub.ready.add_done_callback(self.list_loaded)
        self.take_the_log()
        self.load_batch()

    def on_unmount(self) -> None:
        self.give_back_the_log()
        self.hub.close()

    # -- the log panel: what bdnex is doing (reads, fetches, links), so that a wait is never silent
    def take_the_log(self) -> None:
        """The log goes to the panel: a handler writing to the terminal would draw under the screen."""
        import sys

        wanted = logging.DEBUG if self.verbose >= 2 else logging.INFO
        own = logging.getLogger("bdnex")
        self._saved = (own.level, [(owner, handler) for owner in (logging.getLogger(), own) for handler in owner.handlers
                                    if getattr(handler, "stream", None) in (sys.stderr, sys.stdout, sys.__stderr__, sys.__stdout__)
                                    and not isinstance(handler, logging.FileHandler)])
        for owner, handler in self._saved[1]:
            owner.removeHandler(handler)
        self.panel = LogPanel(self, wanted)
        own.addHandler(self.panel)
        own.setLevel(min(own.level or logging.WARNING, wanted) if own.level else wanted)

    def give_back_the_log(self) -> None:
        own = logging.getLogger("bdnex")
        if getattr(self, "panel", None) is not None:
            own.removeHandler(self.panel)
        level, handlers = getattr(self, "_saved", (own.level, []))
        own.setLevel(level)
        for owner, handler in handlers:
            owner.addHandler(handler)

    def log_from_anywhere(self, text: str) -> None:
        import threading

        if threading.get_ident() == self.ui_thread:
            self.log_line(text)
        else:
            self.call_from_thread(self.log_line, text)

    def log_line(self, text: str) -> None:
        try:
            self.query_one("#log", RichLog).write(Text(f"{time.strftime('%H:%M:%S')} {text}", no_wrap=False))
        except Exception:  # the screen is closing
            pass

    def action_toggle_log(self) -> None:
        panel = self.query_one("#log", RichLog)
        panel.display = not panel.display

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
                                        self.everything, self.again, set(self.shown))
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
        self.shown.update(row.path for row in rows)
        self.marked.clear()
        self.order = {id(row): place for place, row in enumerate(rows)}
        self.sort_column, self.sort_reverse = None, False
        self.timing = f"weighed in {seconds:.1f} s"
        self.render_table()
        if not self.rows:
            self.sub_title = "nothing left to check"
            self.say("Nothing left to check: every doubtful file has been looked at. "
                     "`bdnex import --skipped --auto=all` decides the unlinked ones again.", "green")

    def add_columns(self) -> None:
        """The columns, with an arrow on the sorted one. Every column has a width of its own: a Textual ``DataTable`` measures
        auto-width columns late (Textualize/textual#6759), so a table that is rebuilt (here: by a sort) could paint its first rows
        with stale widths. Long names are cut, so the scores and the state are always on screen."""
        table = self.query_one(DataTable)
        for index, (name, width) in enumerate(COLUMNS):
            arrow = (" ▼" if self.sort_reverse else " ▲") if index == self.sort_column else ""
            table.add_column(f"{name}{arrow}", width=width, key=str(index))

    def render_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        self.add_columns()
        for number, row in enumerate(self.rows, start=1):
            table.add_row(*self.cells(number, row), key=str(number - 1))
        self.update_title()
        if self.rows:
            table.move_cursor(row=next((i for i, row in enumerate(self.rows) if not row.status), 0))
            self.show_detail(table.cursor_row)

    # -- sorting: a click on a header, or the number of the column
    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        self.action_sort(event.column_index)

    def sort_key(self, index: int, row: batch.Row):
        """What the column ``index`` sorts by; ``None`` puts the row after the others, whatever the direction."""
        import os

        if index == 0:
            return self.order.get(id(row), 0)
        if index == 1:
            return os.path.basename(row.path).casefold()
        if index == 2:
            return row.folder.casefold()
        if index == 3:
            return row.album.casefold() if row.album else None
        if index == 4:
            return int(page_number(row.album_url)) if page_number(row.album_url) else None
        if index == 5:
            return row.score
        if index == 6:
            return row.cover
        return DECIDED_ORDER.get(row.status) if row.status else STATE_ORDER.get(row.kind, 5)

    def action_sort(self, index: int) -> None:
        """Sort by the column ``index``; the same column again reverses it. ``#`` is the order of the batch."""
        if not (0 <= index < len(COLUMNS)) or not self.rows:
            return
        self.sort_reverse = (not self.sort_reverse) if index == self.sort_column else False
        self.sort_column = index
        keep = self.current()
        keys = [(self.sort_key(index, row), row) for row in self.rows]
        present = sorted((pair for pair in keys if pair[0] is not None), key=lambda pair: pair[0], reverse=self.sort_reverse)
        self.rows = [row for _, row in present] + [row for key, row in keys if key is None]  # without a value: last
        self.render_table()
        if keep is not None:
            self.query_one(DataTable).move_cursor(row=self.rows.index(keep))

    def update_title(self) -> None:
        done = sum(1 for row in self.rows if row.status)
        by = f" · by {COLUMNS[self.sort_column][0].lower()} {'▼' if self.sort_reverse else '▲'}" if self.sort_column is not None else ""
        self.sub_title = f"batch {self.number} · {done}/{len(self.rows)} checked{by} · {self.timing}".rstrip(" ·")

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
        return [Text(str(number), style="bold reverse magenta" if id(row) in self.marked else "cyan"), Text(os.path.basename(row.path), style="bold"),
                Text(row.folder, style="dim"), album_text(row), page_text(row.album_url), match, score_text(row.cover), state]

    def show_detail(self, index: int) -> None:
        if not (0 <= index < len(self.rows)):
            return
        row = self.rows[index]
        text = Text()
        text.append(row.path + "\n", style="dim")
        text.append(KIND_WORD.get(row.kind, row.kind), style=KIND_STYLE.get(row.kind, "")).append("   now: ").append(album_text(row))
        text.append("\n")
        if row.album_url:  # a link: ctrl/cmd-click in most terminals, or `p`
            text.append(f"  {row.album_url}\n", style=Style(color="cyan", link=row.album_url))
        if row.facts:
            text.append("".join(f"  {line}\n" for line in row.facts.split("\n")))
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
        if row not in self.rows:  # a link that ended after the next batch was loaded: the row is not on the screen any more
            return
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

    # -- several files at once: mark them (space), then give them the series of an album
    def action_mark(self) -> None:
        row = self.current()
        if row is None or row.busy:
            return
        self.marked.symmetric_difference_update({id(row)})
        self.refresh_row(row)
        self.notify(f"{len(self.marked)} file(s) marked: `a` attaches them to a series", timeout=3)
        table = self.query_one(DataTable)
        table.move_cursor(row=min(table.cursor_row + 1, len(self.rows) - 1))

    def action_edit(self) -> None:
        """An album written by hand for the file under the cursor, or one series for the marked files (a comic the site does not know)."""
        from bdnex.library import manual

        row = self.current()
        if row is None or row.busy:
            return
        marked = [r for r in self.rows if id(r) in self.marked and not r.busy]
        if len(marked) >= 2:
            files = [(r.path, manual.guess(r.path)["volume"]) for r in marked]
            values = {**manual.guess(marked[0].path), "volume": ""}
            fields = [f for f in manual.FIELDS if f[0] in manual.SHARED]
            heading = f"A series for these {len(marked)} files (one album each, with the volume in its name)"
            self.push_screen(EditScreen(heading, values, fields, [(p, int(v) if v else None) for p, v in files]),
                             lambda got: self.save_series(marked, got) if got else None)
            return
        file = self.library.get_file(row.path) or {}
        album_id = file.get("album_id")
        if album_id is not None and not manual.is_manual(self.library, album_id):
            self.notify("This file has an album of the site: `u` unlinks it first, or `bdnex edit` changes its fields.", severity="warning")
            return
        values = manual.form_of(self.library, album_id) if album_id is not None else {**manual.guess(row.path), "title": ""}
        self.push_screen(EditScreen(f"Metadata of {os.path.basename(row.path)}", values, manual.FIELDS),
                         lambda got: self.save_one(row, album_id, got) if got else None)

    def save_one(self, row: batch.Row, album_id, values: dict) -> None:
        from bdnex.library import manual

        try:
            if album_id is not None:
                manual.update(self.library, album_id, values)
            else:
                album_id = manual.create(self.library, row.path, values)
        except manual.FormError as err:
            self.notify(str(err), severity="error")
            return
        batch.record_manual(self.library, row, album_id)
        self.decided["linked"] = self.decided.get("linked", 0) + 1
        logger.info("%s: %s", os.path.basename(row.path), row.message)
        self.notify(row.message, timeout=5)
        self.marked.discard(id(row))
        self.refresh_row(row)
        self.advance()

    def save_series(self, rows: list, values: dict) -> None:
        from bdnex.library import manual

        numbers = values.pop("__volumes__")
        try:
            ids = manual.create_series(self.library, [(r.path, n) for r, n in zip(rows, numbers)], values)
        except manual.FormError as err:
            self.notify(str(err), severity="error")
            return
        for row, album_id in zip(rows, ids):
            batch.record_manual(self.library, row, album_id)
            self.marked.discard(id(row))
            self.refresh_row(row)
        self.decided["linked"] = self.decided.get("linked", 0) + len(rows)
        logger.info("%d file(s) written by hand as the series %s", len(rows), values.get("series"))
        self.notify(f"{len(rows)} file(s) linked to the series {values.get('series')!r} (written by hand)", timeout=6)
        self.advance()

    def action_mark_all(self) -> None:
        """Mark every file not decided yet (and not being linked); when they are all marked already, unmark them."""
        free = [r for r in self.rows if not r.status and not r.busy]
        if not free:
            return
        if all(id(r) in self.marked for r in free):
            self.action_mark_none()
            return
        self.marked.update(id(r) for r in free)
        for row in self.rows:
            self.refresh_row(row)
        self.notify(f"{len(self.marked)} file(s) marked: `a` attaches them to a series", timeout=3)

    def action_mark_none(self) -> None:
        self.marked.clear()
        for row in self.rows:
            self.refresh_row(row)
        self.notify("no file marked", timeout=3)

    def action_attach(self) -> None:
        """Pick an album of a series: each marked file (without a mark: the files of this folder not decided yet) gets the
        album of that series with the volume number in its name."""
        chosen = self.files_to_attach()
        if not chosen:
            return
        self.push_screen(FuzzyScreen(self.hub, self.search_start(chosen[0]),
                                     f"An album of the series, or the address of the series page, for these {len(chosen)} file(s)", series_ok=True),
                         lambda candidate: self.run_worker(self.propose(chosen, candidate)) if candidate is not None else None)

    def files_to_attach(self) -> list:
        row = self.current()
        if row is None:
            return []
        chosen = [r for r in self.rows if id(r) in self.marked and not r.busy]
        if not chosen:
            chosen = [r for r in self.rows if r.folder == row.folder and not r.status and not r.busy]
        if not chosen:
            self.notify("Nothing to attach: mark files with space.", severity="warning")
        return chosen

    async def propose(self, chosen: list, candidate) -> None:
        """``candidate``: an album of the series, or the address (text) of the series page."""
        paths = [r.path for r in chosen]
        if isinstance(candidate, str):
            self.notify("Reading the series page (from the library, or fetched once)...", timeout=6)
            try:
                page = await asyncio.to_thread(batch.read_series_address, self.settings, candidate)  # may fetch: not in the screen's thread
                found = await asyncio.wrap_future(self.hub.run(
                    lambda hub: batch.propose_for_series_address(hub.source, candidate, paths, found=page)))
            except batch.NoVolumeFound as err:
                self.notify(str(err), severity="warning", timeout=12)
                logger.info("%s", err)
                return
            except Exception as err:
                self.notify(f"Could not read that series page: {err}", severity="error")
                return
        else:
            found = await asyncio.wrap_future(self.hub.run(lambda hub: batch.propose_for_series(hub.source, candidate, paths)))
        if found is None:
            self.notify("That album is not in a series, or no file name has a volume number (T03, Tome 3...).", severity="warning")
            return
        pairs = [(r, found.mapping[r.path]) for r in chosen if r.path in found.mapping]
        lines = [f"{os.path.basename(r.path)}  ->  {c.url.rsplit('/', 1)[-1].removesuffix('.html')}" for r, c in pairs[:8]]
        more = f"\n... and {len(pairs) - 8} more" if len(pairs) > 8 else ""
        left = len(chosen) - len(pairs)
        question = (f"{found.name}: {len(pairs)} of {len(chosen)} file(s) find their volume"
                    + (f" ({left} left as they are)" if left else "") + ". Link them?\n" + "\n".join(lines) + more)
        self.push_screen(AskScreen(question, None), lambda yes: self.link_many(pairs) if yes else None)

    def link_many(self, pairs: list) -> None:
        """Link each file to its album, one after the other (an import at a time: they write to the same library)."""
        todo = []
        for row, candidate in pairs:
            before = (self.library.get_file(row.path) or {}).get("album_id")
            row.busy, row.message = True, "waiting to be linked..."
            row.link_id += 1
            self.marked.discard(id(row))  # linking now: the marks are free for the next series
            self.refresh_row(row)
            todo.append((row, candidate, before, row.link_id))

        def work() -> None:
            from bdnex.ui import verdicts

            for row, candidate, before, mine in todo:
                if row.link_id != mine:
                    continue  # decided again in the meantime
                self.call_from_thread(self.say_linking, row, candidate)
                captured, error = LineSink(self), ""
                try:
                    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                        verdicts.rematch(row.path, candidate.url, True, max(1, self.verbose))
                except BaseException as err:
                    error = str(err) or err.__class__.__name__
                self.call_from_thread(self.linked, row, before, error, None, mine)

        self.run_worker(work, thread=True, exclusive=False, group="link")

    def say_linking(self, row: batch.Row, candidate) -> None:
        from bdnex.ui.prompts import display_name

        row.message = f"linking to {display_name(candidate)}: reading the album page, a few seconds..."
        self.refresh_row(row)
        self.watch_link(row)

    def action_cover(self) -> None:
        """A popup with the file's cover and the album's saved cover, side by side."""
        row = self.current()
        if row is None:
            return
        files = batch.cover_files(self.library, self.settings, row)
        self.push_screen(CoverScreen(f"{os.path.basename(row.path)}  ·  {row.album or '(no album)'}", files))

    def action_page(self) -> None:
        row = self.current()
        if row is None or not row.album_url:
            return
        opened = False
        if not os.environ.get("SSH_CONNECTION"):  # over SSH a browser would open on the other machine (or a text one here)
            try:
                opened = webbrowser.open(row.album_url)
            except Exception:
                opened = False
        if not opened:
            self.copy_to_clipboard(row.album_url)  # the terminal's clipboard (OSC 52), where it supports it
            self.notify(f"{row.album_url}\n(copied; or ctrl/cmd-click the link in the detail or the Page column)", timeout=10)

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
            if is_series_address(text):  # a series, not an album: the files of the folder (or the marked ones) get their volumes
                chosen = self.files_to_attach()
                if chosen:
                    await self.propose(chosen, text.strip())
                return
            candidate = await asyncio.wrap_future(self.hub.run(lambda hub: hub.source.candidate_for(text)))
            if candidate is None:
                self.notify(f"{text!r} is not an album: give the address of its page, or its number.", severity="error")
                return
            self.link(row, candidate.url, candidate)

        self.push_screen(AskScreen("The album's page address, or its number:", ""),
                         lambda text: self.run_worker(given(text)) if text else None)

    # -- linking: it reads an album page, so it runs in a thread and the table says so
    def link(self, row: batch.Row, url: str, candidate=None, keep_both: bool = True) -> None:
        """``keep_both``: also when the album has another file (this one becomes a spare version; `bdnex duplicates` decides later)."""
        import os

        from bdnex.ui.prompts import display_name

        name = display_name(candidate) if candidate is not None else url
        before = (self.library.get_file(row.path) or {}).get("album_id")
        row.busy, row.message = True, f"linking to {name}: reading the album page, a few seconds..."
        row.link_id += 1
        self.refresh_row(row)
        self.watch_link(row)
        self.notify(f"Linking {os.path.basename(row.path)} to {name}...", timeout=4)
        logger.info("linking %s to %s (the album and series pages come from the library, or the fetcher)", os.path.basename(row.path), name)

        mine = row.link_id

        def work() -> None:
            from bdnex.ui import verdicts

            captured, error = LineSink(self), ""
            try:  # the import prints its progress: it must not land on the screen
                with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                    verdicts.rematch(row.path, url, keep_both, max(1, self.verbose))
            except BaseException as err:  # a SystemExit of the import must not leave the row waiting for ever
                error = str(err) or err.__class__.__name__
            self.call_from_thread(self.linked, row, before, error, (url, candidate, keep_both), mine)

        self.run_worker(work, thread=True, exclusive=False, group="link")

    LINK_WAIT = 120  # seconds: a link that takes longer is not waited for any more (the screen must never be stuck)

    def watch_link(self, row: batch.Row) -> None:
        mine = row.link_id
        self.set_timer(self.LINK_WAIT, lambda: self.gave_up(row, mine))

    def gave_up(self, row: batch.Row, link_id: int) -> None:
        if row.busy and row.link_id == link_id:
            row.busy, row.link_id = False, row.link_id + 1  # its late answer is ignored
            row.message = f"no answer after {self.LINK_WAIT} s (the site or the fetcher is slow?): not linked, decide again"
            self.notify(f"{os.path.basename(row.path)}: {row.message}", severity="warning", timeout=10)
            self.refresh_row(row)

    def linked(self, row: batch.Row, before, error: str = "", asked=None, link_id=None) -> None:
        import os

        if link_id is not None and link_id != row.link_id:
            return  # the screen gave up on this link; the row was decided again since
        row.busy = False
        self.marked.discard(id(row))
        if error:
            row.message = f"failed: {error}"
            self.notify(f"{os.path.basename(row.path)}: {row.message}", severity="error")
            self.refresh_row(row)
            return
        ok, message = batch.finish_link(self.library, row, before, asked[0] if asked else None)
        logger.info("%s: %s", os.path.basename(row.path), message)
        if ok:
            file = self.library.get_file(row.path) or {}
            row.cover, row.score, row.kind = file.get("cover_score"), None, "ok"
            row.why = ["+ chosen by you"]
            row.message = message + (f" · its cover is {row.cover:.0f}% like the file's" if row.cover is not None
                                     else " · cover not compared")
            self.decided["linked"] = self.decided.get("linked", 0) + 1
        self.notify(row.message, severity="information" if ok else "error", timeout=6)
        self.refresh_row(row)
        if row not in self.rows:
            return
        if ok:
            self.advance()
        elif asked is not None and not asked[2]:
            self.offer_both(row, *asked[:2])

    def offer_both(self, row: batch.Row, url: str, candidate) -> None:
        """The album has another file already: keep both (this file becomes a spare version), or leave it."""
        from bdnex.ui.prompts import display_name

        name = display_name(candidate) if candidate is not None else url
        question = (f"{name} already has another file. Link {row.path.rsplit('/', 1)[-1]} to it too, as a spare version "
                    "(`bdnex duplicates` decides later which one stays)?")
        self.push_screen(AskScreen(question, None),
                         lambda yes: self.link(row, url, candidate, keep_both=True) if yes else None)

    def action_next_batch(self) -> None:
        self.load_batch()


def run(library, settings, source, size: int = 50, terms=(), unmatched: bool = False, everything: bool = False,
        again: bool = False, verbose: int = 0) -> dict[str, int]:
    """Run the table until the person quits; returns how many files were decided, by decision."""
    app = ReviewApp(library, settings, source, size, terms, unmatched, everything, again, verbose)
    app.run()
    return app.decided
