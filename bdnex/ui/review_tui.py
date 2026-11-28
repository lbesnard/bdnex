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
from functools import partial
import io
import logging
import os
import subprocess
import re
import shutil
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from bdnex.ui.review_modal import Modal
from textual.widgets import TabbedContent, TabPane, Button, Checkbox, DataTable, Footer, Header, Input, Label, OptionList, RichLog, Select, Static, TextArea
from textual.widgets.option_list import Option

from bdnex.library import browse, review_batch as batch
from bdnex.ui import color
from bdnex.ui.review_extras import ReviewExtras
from bdnex.ui.review_organise import ReviewOrganise
from bdnex.ui.review_volumes import VolumesScreen
from bdnex.ui.cover_text import GRAPHICS, graphic
from bdnex.ui import review_panels as panels
from bdnex.ui.review_actions import ActionsChip, ReviewActions
from bdnex.ui.review_comicvine import ReviewComicvine
from bdnex.ui.review_panels import FacetCommands, HelpChip, PagerChip, Splitter, legend

STATUS_STYLE = {"keep": "green", "linked": "green", "unlinked": "red", "left": "green", "skipped": "yellow"}
STATUS_WORD = {"keep": "kept", "linked": "linked", "unlinked": "unlinked", "left": "no album online", "skipped": "skipped"}  # what a decision of this session is called
KIND_STYLE = {"wrong": "red", "unconfirmed": "yellow", "unmatched": "magenta", "ok": "green"}
KIND_COLOUR = {"wrong": "red", "unconfirmed": "yellow", "unmatched": "magenta", "ok": "green"}  # (the badge: dark text on this colour)
KIND_WORD = {"wrong": "looks wrong", "unconfirmed": "to check", "unmatched": "no album", "ok": "looks right"}  # (what bdnex thinks of the match; `reviewed` is what you did)
EVIDENCE_STYLE = {"-": "red", "+": "green", "?": "yellow", "f": "yellow"}
SEARCH_LIMIT = 60
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # braille dots turning: a state that is waiting for the site
from bdnex.ui.review_columns import COLUMN_TYPES, COLUMNS, DEFAULT_VIEW, FLEX_COLUMN, ColumnsMixin, ReviewTable  # noqa: E402,F401
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
        self.cache: dict = {}  # the albums of the words typed so far (see `album_search`): one more letter is a quick search
        self.series: Optional[list] = None  # the series of the letter index, loaded when a search wants them
        self.series_folded: list[str] = []
        self.series_cache: dict = {}
        build = source if callable(source) and not hasattr(source, "candidates") else (lambda: source)
        self.ready = self.pool.submit(self._build, build)
        self.loaded = None  # the album list loaded (`load_list`): a Future, once it is asked for

    def _build(self, build) -> None:
        made = build()
        self.source, self.close_it = made if isinstance(made, tuple) else (made, None)

    def load_list(self):
        """Load the album list (about three seconds of Python) now. Asked for once the table is on screen, not before: it used to run beside
        the weighing of the first batch and both took a few times longer (two threads share one interpreter). A search queued behind it waits."""
        if self.loaded is None:
            def warm(_hub) -> None:
                warm_it = getattr(self.source, "warm", None)
                if warm_it:
                    warm_it()

            self.loaded = self.run(warm)
        return self.loaded

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
    """What an import prints while it runs in a thread: each line goes to the panel. Nothing is kept (a long import prints a lot, and the
    text was never read back)."""

    def __init__(self, app):
        super().__init__()
        self.app, self.pending = app, ""

    def write(self, text: str) -> int:
        self.pending += text.replace("\r", "\n")
        *lines, self.pending = self.pending.split("\n")
        for line in lines:
            if line.strip():
                self.app.log_from_anywhere(line.rstrip())
        return len(text)


SERIES_ADDRESS = re.compile(r"bedetheque\.com/serie-\d+", re.I)


def is_series_address(text: str) -> bool:
    """The address of a series page, not of an album: it is not in the album list (that lists albums), the series page has them."""
    return bool(SERIES_ADDRESS.search(text or ""))


def fuzzy_search(hub: SourceHub, text: str, limit: int = SEARCH_LIMIT, series: bool = False, albums: bool = True) -> tuple[list, int]:
    """The albums of the whole list that look like ``text``, best first, as ``([(candidate, score)], matches)``.

    Every word has to be in the name (case and accents do not matter); when that finds few, a fuzzy match over the whole
    list (letters missing, a typo) takes over: what ``fzf`` does, in the screen. Runs in the thread of the album list.
    ``series``: the series of the letter index are searched too and listed with the albums (their address is a series page).
    ``albums=False`` (with ``series``): the series only. Each kind is searched on its own, so one that has many hits cannot push the other
    out of the best ``limit`` (a name with many series would show no album at all)."""
    from bdnex.importer import album_search

    if series and hasattr(hub.source, "series_catalogue"):
        found_albums, total = fuzzy_search(hub, text, limit) if albums else ([], 0)
        if hub.series is None:
            hub.series = list(hub.source.series_catalogue())
            hub.series_folded = album_search.folded_names(hub.series)
        found, more = album_search.search_folded(hub.series, hub.series_folded, text, limit, hub.series_cache)
        merged = sorted([*found, *found_albums], key=lambda pair: -pair[1])  # a series ahead of an album with the same score (stable sort)
        return merged[:limit], total + more

    if not hasattr(hub.source, "catalogue"):  # a source that cannot list its albums: its own search
        return [pair for pair in hub.source.candidates(text.strip(), limit=limit)], 0
    if hub.catalogue is None:
        hub.catalogue = list(hub.source.catalogue())
        hub.folded = album_search.folded_names(hub.catalogue)
    return album_search.search_folded(hub.catalogue, hub.folded, text, limit, hub.cache)


CANDIDATES_SHOWN = 8
VERDICT_STYLE = {"confirmed": "green", "contradicted": "red", "doubtful": "yellow", "unverified": "dim"}


def candidates_of(hub: SourceHub, path: str, album_id: Optional[int], limit: int = CANDIDATES_SHOWN) -> list:
    """The few albums this file could be, best first, as ``[(candidate, entry)]``: what ``bdnex candidates`` lists (the matcher's own
    ranking, with the evidence of each score), not a search of the whole album list. Runs in the thread of the album list."""
    source = hub.source
    if hasattr(source, "library") and hasattr(source, "explain"):
        from bdnex.importer.candidates import describe_candidate
        from bdnex.importer.matching import find_matches

        warm = getattr(source, "warm", None)
        if warm:
            warm()
        file = source.library.get_file(path) or {"path": path}
        return [(m.candidate, describe_candidate(source.library, source, m, file, album_id)) for m in find_matches(source, path, limit=limit)]
    from bdnex.importer.matching import query_from_filename  # a source that only searches by text: its best guesses, without evidence

    return [(c, {"score": round(s), "album": c.name, "url": c.url, "year": None, "publisher": None, "complete": False, "current": False,
                 "why_score": [], "verdict": "unverified", "for": [], "against": []})
            for c, s in source.candidates(query_from_filename(path), limit=limit)]


def open_in_browser(url: str) -> bool:
    """Open a page in the person's browser when there is one to open: over SSH a browser would start on the other machine (or a text one
    inside this terminal), and on a machine with no display `webbrowser` may start `lynx` on the tty of the screen. ``False``: do not."""
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def show_address(app, url: str) -> None:
    """The address where a browser can not be opened: copied (the terminal's clipboard) and said."""
    app.copy_to_clipboard(url)
    app.notify(f"{url}\n(copied; or ctrl/cmd-click the link in the detail or the Page column)", timeout=10)


@dataclass
class Waiting:
    """A question in the queue: what to say about it, how to build its window, and what to do with the answer."""

    title: str
    make: Callable
    callback: Callable


class CandidatesScreen(Modal):
    """The albums this one file could be, best first, with where each score comes from: Enter links the highlighted one.
    Few and quick (the matcher's own list); `o` goes on to the search of the whole album list."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("o", "search", "Search all albums"), Binding("ctrl+o", "open", "Open the page"),
                Binding("down", "move(1)", show=False, priority=True), Binding("up", "move(-1)", show=False, priority=True)]
    CSS = """
    CandidatesScreen { align: center middle; }
    #cands { width: 90%; height: 85%; border: round $accent; padding: 1 2; background: $surface; }
    #choices { height: auto; max-height: 12; }
    #evidence { height: 1fr; margin-top: 1; }
    """

    def __init__(self, hub: SourceHub, path: str, album_id: Optional[int], title: str):
        super().__init__()
        self.hub, self.path, self.album_id, self.heading = hub, path, album_id, title
        self.found: list = []

    def compose(self) -> ComposeResult:
        with Vertical(id="cands"):
            yield Label(Text(self.heading, style="bold"))
            yield Label("looking...", id="state")
            yield OptionList(id="choices")
            yield Static("", id="evidence")
            yield Label(Text("Enter links it · ↑↓ move · Ctrl-O opens its page · o searches every album · Esc goes back", style="dim"))

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        self.run_worker(self.find())

    async def find(self) -> None:
        try:
            found = await asyncio.wrap_future(self.hub.run(lambda hub: candidates_of(hub, self.path, self.album_id)))
        except Exception as err:  # the album list could not be loaded
            if self.is_attached:
                self.query_one("#state", Label).update(Text(f"the album list is not available: {err}", style="red"))
            return
        if not self.is_attached or not self.query("#choices"):
            return
        self.found = found
        options = self.query_one(OptionList)
        options.add_options([Option(self.line(entry)) for _, entry in found])
        self.query_one("#state", Label).update(Text(f"{len(found)} candidate(s)" if found else "nothing in the album list looks like this file: try `o`",
                                                    style="dim" if found else "yellow"))
        if found:
            options.highlighted = 0

    @staticmethod
    def line(entry: dict) -> Text:
        text = Text(f"{entry['score']:>3}  ")
        text.append("* " if entry["current"] else "  ")
        text.append(entry["album"], style="bold")
        extra = " ".join(str(part) for part in (entry["year"], entry["publisher"]) if part)
        if extra:
            text.append(f"  {extra}", style="dim")
        verdict = entry["verdict"] + ("" if entry["complete"] else ", page not read")
        text.append(f"  [{verdict}]", style=VERDICT_STYLE.get(entry["verdict"], "dim"))
        return text

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_index is None or not 0 <= event.option_index < len(self.found):
            return
        candidate, entry = self.found[event.option_index]
        lines = [Text("where the score comes from", style="dim"), *[Text(f"  = {why}") for why in entry["why_score"]]]
        lines += [Text(f"  + {text}", style="green") for text in entry["for"]] + [Text(f"  - {text}", style="red") for text in entry["against"]]
        lines.append(Text(candidate.url, style="dim"))
        self.query_one("#evidence", Static).update(Text("\n").join(lines))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if 0 <= event.option_index < len(self.found):
            self.dismiss(self.found[event.option_index][0])

    def action_move(self, step: int) -> None:
        options = self.query_one(OptionList)
        if self.found:
            options.highlighted = max(0, min(len(self.found) - 1, (options.highlighted or 0) + step))

    def action_open(self) -> None:
        index = self.query_one(OptionList).highlighted
        if index is not None and 0 <= index < len(self.found):
            url = self.found[index][0].url
            if not open_in_browser(url):
                show_address(self.app, url)

    def action_search(self) -> None:
        self.dismiss("search")

    def action_back(self) -> None:
        self.dismiss(None)


class Tick(Checkbox):
    """A tick box that the keyboard leaves alone (the text field keeps the focus: F2 / F3 or a click toggle it)."""

    can_focus = False


class FuzzyScreen(Modal):
    """Search the whole album list as you type (like ``fzf``): Enter picks, Ctrl-O opens the page, Esc goes back."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("ctrl+o", "open", "Open the page"),
                Binding("f2", "tick('series')", "Series on/off", show=False), Binding("f3", "tick('albums')", "Albums on/off", show=False),
                Binding("f4", "comicvine", "Search Comic Vine", show=False),
                Binding("down", "move(1)", show=False, priority=True), Binding("up", "move(-1)", show=False, priority=True)]
    CSS = """
    FuzzyScreen { align: center middle; }
    #tocv { margin-top: 1; }
    #search { width: 90%; height: 85%; border: round $accent; padding: 1 2; background: $surface; }
    #hits { height: 1fr; }
    #kinds { height: 3; }
    #kinds Checkbox { width: auto; margin-right: 3; border: none; padding: 0 1; }
    """

    def __init__(self, hub: SourceHub, start: str, title: str, series_ok: bool = False, about: Optional[list[str]] = None):
        super().__init__()
        self.hub, self.start, self.heading, self.series_ok = hub, start, title, series_ok
        self.about = about or []  # the folder and name of the file(s) this is for: they help to find the album, and are not on this screen otherwise
        self.hits: list = []
        self.knowledge: dict = {}  # (id, is a series) -> (author, year) the library has
        self.show_series = self.show_albums = True  # the two tick boxes (only when series can be picked)
        self.text = start

    def compose(self) -> ComposeResult:
        with Vertical(id="search"):
            yield Label(Text(self.heading, style="bold"))
            if self.about:
                yield Label(Text("\n".join(self.about), style="cyan"), id="about")
            yield Input(value=self.start, id="words", placeholder=(
                "words of the album, in any order, or the address of the series page" if self.series_ok else "type to find the album: words, in any order"))
            if self.series_ok:
                with Horizontal(id="kinds"):  # what is searched (not only what is shown): many series cannot push every album out of the list
                    yield Tick("SERIES  (F2)", True, id="show-series")
                    yield Tick("Albums  (F3)", True, id="show-albums")
            yield Button("Search Comic Vine instead  (F4)", id="tocv",
                         tooltip="Look for the series on Comic Vine (one request, by name) instead of in Bédéthèque's list: the volume you pick is used like a pasted address.")
            yield Label("", id="count")
            if self.series_ok:
                yield Label(Text("score  type    year  author                  name   (a SERIES gives each file its volume; an album is just that one book)", style="dim"))
            yield OptionList(id="hits")
            yield Static("", id="preview")
            yield Label(Text("Enter picks the album · Ctrl-O opens its page · ↑↓ move · Ctrl-U clears · Esc goes back", style="dim"))

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self.search(self.start, at_once=True)

    DELAY = 0.15  # seconds of quiet after a key before the search runs: not one search per letter

    def search(self, text: str, at_once: bool = False) -> None:
        self.query_one("#count", Label).update(Text("looking...", style="dim"))
        self.latest = getattr(self, "latest", 0) + 1
        mine = self.latest
        timer = getattr(self, "pending", None)
        if timer is not None:
            timer.stop()
        if at_once:
            self.run_worker(self.find(text, mine), exclusive=True)
        else:
            self.pending = self.set_timer(self.DELAY, lambda: self.run_worker(self.find(text, mine), exclusive=True))

    async def find(self, text: str, mine: int = 0) -> None:
        def look(hub):
            if mine and mine != self.latest:
                return [], 0  # typed over meanwhile: the list's thread does not spend its time on it
            if self.series_ok and not (self.show_series or self.show_albums):
                return [], 0  # both ticks off: nothing is asked for
            return fuzzy_search(hub, text, series=self.series_ok and self.show_series, albums=self.show_albums or not self.series_ok)

        try:
            hits, total = await asyncio.wrap_future(self.hub.run(look))
        except Exception as err:  # the album list could not be loaded
            if self.is_attached:
                self.query_one("#count", Label).update(Text(f"the album list is not available: {err}", style="red"))
            return
        if mine and mine != self.latest:
            return  # a newer search is on its way
        if not self.is_attached or not self.query("#hits"):
            return  # the screen was closed while the search ran (Enter on a pasted address, Esc)
        self.hits = hits
        self.shown = mine  # these hits answer the latest typed text
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options([Option(self.hit_line(candidate, score)) for candidate, score in hits])
        if hits:
            options.highlighted = 0
        self.query_one("#count", Label).update(Text(f"{total} album(s) match" if text.strip() else "", style="dim"))

    def hit_line(self, candidate, score: float) -> Text:
        """One hit: the score, then (when series can be picked) what it is: a whole SERIES, or one album, then the name."""
        from bdnex.ui.prompts import display_name

        line = Text(f"{score:>3.0f}  ")
        series = is_series_address(candidate.url)
        if series and self.series_ok:
            line.append("SERIES  ", style="bold cyan")
        elif self.series_ok:
            line.append("album   ", style="dim")
        author, year = self.known(candidate, series)
        line.append(f"{year:<4}  ", style="dim")  # only what the library already has: an album not fetched yet shows nothing here
        line.append(f"{author[:22]:<22}  ", style="cyan")
        line.append(candidate.name if series else display_name(candidate), style="bold" if series else "")
        return line

    def known(self, candidate, series: bool) -> tuple[str, str]:
        try:
            library = getattr(self.app, "library", None)
        except Exception:  # (a screen not shown yet has no app)
            library = None
        if library is None:
            return "", ""
        key = (candidate.source_id, series)
        if key not in self.knowledge:
            self.knowledge[key] = batch.known_about(library, getattr(self.hub.source, "name", "bedetheque"), candidate.source_id, series)
        return self.knowledge[key]

    def on_input_changed(self, event: Input.Changed) -> None:
        self.text = event.value
        self.search(event.value)

    def action_comicvine(self) -> None:
        """Comic Vine's own search: a volume is picked there and comes back as its address (what a pasted address does)."""
        from bdnex.ui.review_comicvine import ComicvineSearchScreen

        self.app.push_screen(ComicvineSearchScreen(self.query_one("#words", Input).value.strip(), self.about), self.comicvine_picked)

    def comicvine_picked(self, address) -> None:
        # a method that returns nothing, not a lambda that returns `self.dismiss(...)`: Textual awaits what a callback returns, and awaiting the
        # dismissal of a screen from its own message handler is a ScreenError (the whole screen came down)
        if address:
            self.dismiss(address)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tocv":
            event.stop()
            self.action_comicvine()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "show-series":
            self.show_series = event.value
        elif event.checkbox.id == "show-albums":
            self.show_albums = event.value
        self.search(self.text, at_once=True)  # asked again: the other kind may have been cut off the list

    def action_tick(self, kind: str) -> None:
        if self.series_ok:
            box = self.query_one("#show-series" if kind == "series" else "#show-albums", Checkbox)
            box.value = not box.value  # its change searches again

    def on_input_submitted(self, event: Input.Submitted) -> None:
        from bdnex.importer import comicvine

        if self.series_ok and (is_series_address(event.value) or comicvine.is_address(event.value)):
            self.dismiss(event.value.strip())  # the address of a series page (or a Comic Vine volume): its albums are read from that page
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
        if getattr(self, "shown", -1) != getattr(self, "latest", 0):  # the hits on screen are for what was typed BEFORE: not this album
            self.query_one("#count", Label).update(Text("still looking: press Enter again when the list has changed", style="yellow"))
            return
        if index is not None and 0 <= index < len(self.hits):
            picked = self.hits[index][0]
            self.dismiss(picked.url if is_series_address(picked.url) else picked)  # a series is given as its address: its page is read

    def action_open(self) -> None:
        index = self.query_one(OptionList).highlighted
        if index is not None and 0 <= index < len(self.hits):
            url = self.hits[index][0].url
            if not open_in_browser(url):
                show_address(self.app, url)

    def action_back(self) -> None:
        self.dismiss(None)


class AskScreen(Modal):
    """One line of text (an album address or number), or a yes/no question when ``text`` is ``None``."""

    BINDINGS = [Binding("escape", "back", "Back")]
    CSS = """
    AskScreen { align: center middle; }
    #ask { width: 80%; height: auto; border: round $accent; padding: 1 2; background: $surface; }
    #askbuttons { height: 3; align-horizontal: right; }
    #askbuttons Button { margin-left: 2; }
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
            with Horizontal(id="askbuttons"):
                yield Button("Save" if self.text is not None else "Yes", id="askok", variant="success",
                             tooltip="Keep what you typed." if self.text is not None else "Yes (the y key does the same).")
                yield Button("Cancel" if self.text is not None else "No", id="askno",
                             tooltip="Close without changing anything." if self.text is not None else "No (the n key does the same).")

    def on_mount(self) -> None:
        if self.text is not None:
            self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "askok":
            self.dismiss(self.query_one(Input).value.strip() or None if self.text is not None else True)
        else:
            self.dismiss(None if self.text is not None else False)

    def on_key(self, event) -> None:
        if self.text is None and event.key in ("y", "n"):
            event.stop()  # `n` is also the screen's "next batch"
            self.dismiss(event.key == "y")

    def action_back(self) -> None:
        self.dismiss(None)


class EditScreen(Modal):
    """A form for an album written by hand (one file), or for the series of several files: the Save button (or Ctrl-S) accepts, Cancel (Esc, or `x` outside a text field) cancels.

    ``volumes``: for several files, ``[(path, volume or None)]``: one line each, ``volume  file name``, the numbers can be changed."""

    BINDINGS = [Binding("escape", "back", "Cancel"), Binding("x", "back_x", "Cancel", show=False),
                Binding("ctrl+s", "accept", "Save")]  # Ctrl-S stays for who can use it; tmux takes it, hence the buttons
    CSS = """
    EditScreen { align: center middle; }
    #edit { width: 90%; height: 95%; border: round $accent; padding: 1 2; background: $surface; }
    #editfields { height: 1fr; }
    #edit Input { margin-bottom: 0; }
    .editrow { height: 3; }
    .editrow Input { width: 1fr; }
    .versionrow { height: 3; }
    .versionrow Select { width: 40; }
    .versionrow Input { width: 1fr; margin-left: 1; }
    .fieldname { width: 22; padding-top: 1; color: $text-muted; }
    .editrow Button { margin-left: 1; min-width: 9; }
    #volumes { height: 8; }
    #problem { color: $error; height: auto; }
    #buttons { height: 3; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    #edithelp { height: 1; }
    """

    def __init__(self, heading: str, values: dict, fields, volumes=None, originals: Optional[dict] = None,
                 versions: Optional[list[str]] = None):
        """``originals``: what the site says (the form's keys), for the Restore button of each field that has one. ``versions``: the names the
        Version dropdown offers (``None``: the form has no such row)."""
        super().__init__()
        self.versions = versions
        self.heading, self.values, self.fields, self.volumes = heading, values, fields, volumes
        self.originals = originals or {}
        self.restored: set[str] = set()  # fields put back to the site's value (the box still says so when Save is pressed)

    def compose(self) -> ComposeResult:
        with Vertical(id="edit"):
            yield Label(Text(self.heading, style="bold"))
            with VerticalScroll(id="editfields"):  # the fields scroll; Save and Cancel stay at the bottom of the window
                for key, label, hint in self.fields:
                    yield Label(Text(f"{label}" + (f"  ({hint})" if hint else ""), style="dim"))
                    with Horizontal(classes="editrow"):
                        yield Input(value=self.values.get(key, ""), id=f"f-{key}")
                        yield Button("Clear", id=f"clear-{key}", classes="emptier")  # the same button as in the form for many files
                        if key in self.originals and key not in ("series", "volume"):
                            yield Button("Restore", id=f"restore-{key}", classes="emptier")
                if self.versions is not None:
                    yield from panels.version_row(self.versions)
                if self.volumes is not None:
                    yield Label(Text("Volume and file (change a number if it was read wrong)", style="dim"))
                    yield TextArea("\n".join(f"{'' if v is None else v}\t{os.path.basename(p)}" for p, v in self.volumes), id="volumes")
                yield Label("", id="problem")
            yield Label(Text("Tab moves · Save (or Ctrl-S) saves · Cancel, Esc or x (outside a text field) closes", style="dim"), id="edithelp")
            with Horizontal(id="buttons"):
                yield Button("Save", id="ok", variant="success")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        for button in self.query(Button):
            if (button.id or "").startswith("clear-"):
                button.tooltip = "Empty this field (it is saved as none)."
            elif (button.id or "").startswith("restore-"):
                site = self.originals.get(button.id[len("restore-"):], "")
                button.tooltip = "Put back what the site (Bédéthèque) says: " + (f"{site[:80]!r}" if site else "nothing (it has no value)") + "."
            elif button.id == "ok":
                button.tooltip = "Save the changes (they are checked first)."
            elif button.id == "cancel":
                button.tooltip = "Close without changing anything."
        self.query_one(f"#f-{self.fields[0][0]}", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.screen.focus_next()

    def check_action(self, action: str, parameters) -> bool:
        if action == "back_x" and isinstance(self.focused, (Input, TextArea)):
            return False  # `x` is a letter there; Esc has its own action
        return True

    def action_back_x(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "ok":
            self.action_accept()
        elif (event.button.id or "").startswith("clear-"):
            box = self.query_one("#f-" + event.button.id[len("clear-"):], Input)
            box.value = ""  # an empty field is saved as none
            box.focus()
        elif (event.button.id or "").startswith("restore-"):
            box = self.query_one("#f-" + event.button.id[len("restore-"):], Input)
            key = event.button.id[len("restore-"):]
            box.value = self.originals.get(key, "")  # the site's value; your edit of it is forgotten on Save
            self.restored.add(key)
            box.focus()
        else:
            self.dismiss(None)

    def action_accept(self) -> None:
        values = {key: self.query_one(f"#f-{key}", Input).value.strip() for key, _, _ in self.fields}
        back = [key for key in self.restored if key in values and values[key] == self.originals.get(key, "").strip()]  # (typed over since: not a restore)
        if self.volumes is not None:
            lines = self.query_one("#volumes", TextArea).text.split("\n")
            while len(lines) > len(self.volumes) and not lines[-1].strip():
                lines.pop()  # a trailing newline is not a file
            numbers = []
            for line in lines:
                first = line.split("\t", 1)[0].strip()
                if first and not (first.isascii() and first.isdigit()):  # '²' is a digit to isdigit() but not to int()
                    self.query_one("#problem", Label).update(f"volume must be a number: {first!r}")
                    return
                numbers.append(int(first) if first else None)
            if len(numbers) != len(self.volumes):
                self.query_one("#problem", Label).update("keep one line per file")
                return
            values["__volumes__"] = numbers
        from bdnex.library import manual

        try:  # what would be refused after the form is gone (a year that is no year, an empty series) is said here, and nothing typed is lost
            manual.check({k: v for k, v in values.items() if k != "__volumes__"}, series=self.volumes is not None)
        except manual.FormError as err:
            self.query_one("#problem", Label).update(str(err))
            return
        if self.versions is not None:
            if (self.query_one("#versionpick", Select).value or "") == panels.OTHER_VERSION and not panels.chosen_version(self):
                self.query_one("#problem", Label).update("Type the name of the version (on the right of 'Version of the album')")
                self.query_one("#versionname", Input).focus()
                return
            version = panels.chosen_version(self)
            if version:
                values["__version__"] = version
        self.dismiss({**values, "__restore__": back} if back else values)

    def action_back(self) -> None:
        self.dismiss(None)


class Pane(VerticalScroll):
    """A panel of text that scrolls (a Static does not): its text is in ``body``; ``update`` replaces it and goes back to the top."""

    def __init__(self, id: str) -> None:
        super().__init__(id=id)
        self.body = Static("", id=f"{id}-body")

    def compose(self) -> ComposeResult:
        yield self.body

    def update(self, content) -> None:
        self.body.update(content)
        self.scroll_home(animate=False)


class Pictures:
    """What a screen needs to draw a real picture (kitty, iTerm2, sixel) straight on the terminal, past Textual, and to take it away."""

    deletes_kitty = False  # set when the kitty mode draws (its pictures stay on the terminal until deleted)

    def picture_mode(self) -> str:
        return getattr(getattr(getattr(self.app, "settings", None), "cover", None), "picture", "symbols")

    def write_raw(self, data: bytes) -> None:
        """Straight to the terminal, past Textual (it knows nothing of pictures): used only for the real-picture modes."""
        driver = getattr(self.app, "_driver", None)
        if driver is not None:
            driver.write(data.decode("utf-8", "replace"))
            flush = getattr(driver, "flush", None)
            if flush:
                flush()

    def place(self, sequence: bytes, x: int, y: int) -> None:
        """The picture goes at screen cell ``(x, y)``, its top left; the cursor is put back where it was."""
        self.write_raw(b"\x1b7" + f"\x1b[{y + 1};{x + 1}H".encode() + sequence + b"\x1b8")

    def clear_graphic(self) -> None:
        """A kitty picture stays until it is deleted (sixel and iTerm2 pictures are painted over by the next draw)."""
        if self.deletes_kitty:
            delete = b"\x1b_Ga=d,d=A\x1b\\"  # every picture, and its data
            self.write_raw(delete)  # as it is: a tmux that keeps the pictures itself (recent versions) takes this one
            if os.environ.get("TMUX"):  # and wrapped, for the terminal behind a tmux that only passes it on
                self.write_raw(b"\x1bPtmux;\x1b" + delete.replace(b"\x1b", b"\x1b\x1b") + b"\x1b\\")


    def redraw_after(self) -> None:
        """A real picture was shown: once it is closed the terminal is cleared and the whole screen drawn again. Textual only sends the cells
        that changed, so it never touches the ones a picture sits on, and tmux (or the terminal) keeps the picture there; clearing the
        terminal (ESC [ 2 J) takes it away, and the full repaint puts the app back. ``cover.redraw_after_picture: false`` (the default) turns it
        off. Never an error: it is only a help."""
        from bdnex.ui.cover_text import GRAPHICS

        if self.picture_mode() not in GRAPHICS or not getattr(getattr(getattr(self.app, "settings", None), "cover", None), "redraw_after_picture", False):
            return
        app = self.app

        def repaint() -> None:
            with contextlib.suppress(Exception):
                self.write_raw(b"\x1b[2J")
                app.refresh(layout=True)
                app.screen.refresh(layout=True)
            if os.environ.get("TMUX"):
                with contextlib.suppress(Exception):
                    subprocess.Popen(["tmux", "refresh-client"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        app.set_timer(0.15, repaint)  # (after the screen under the popup is ready)


class PageScreen(Pictures, Modal):
    """One picture as large as the terminal allows, to read the credits: the pages of the file (left / right), or the album's saved cover (Tab).
    It is text (chafa when installed, else half blocks), so it works over SSH and in tmux."""

    BINDINGS = [Binding("escape", "close", "Back"), Binding("z", "close", "Back", show=False), Binding("q", "close", "Back", show=False),
                Binding("right", "turn(1)", "Next page"), Binding("left", "turn(-1)", "Previous page"),
                Binding("tab", "swap", "File / album cover"),
                Binding("plus", "zoom(1)", "Zoom in"), Binding("equals_sign", "zoom(1)", "Zoom in", show=False), Binding("minus", "zoom(-1)", "Zoom out"),
                Binding("up", "pan(0, -1)", "Up", show=False), Binding("down", "pan(0, 1)", "Down", show=False),
                Binding("h", "pan(-1, 0)", "Left", show=False), Binding("l", "pan(1, 0)", "Right", show=False),
                Binding("k", "pan(0, -1)", "Up", show=False), Binding("j", "pan(0, 1)", "Down", show=False)]
    CSS = """
    PageScreen { align: center middle; background: $surface; }
    #pageview { width: 100%; height: 100%; }
    #picture { width: 100%; height: 1fr; content-align: center middle; }
    #pagehead, #pagefoot { height: 1; padding: 0 1; }
    """

    def __init__(self, title: str, files) -> None:
        super().__init__()
        self.title_text, self.files = title, files  # files: (path, format, saved cover or None)
        self.index, self.total, self.which, self.token = 0, 0, "file", 0
        self.deletes_kitty = False  # set when the kitty mode draws (its pictures stay on the terminal until deleted)
        self.zoom, self.px, self.py, self.step = 1, 0.0, 0.0, (0.0, 0.0)  # zoom 1 is the whole page; above it a part of the page, moved with the arrows

    def compose(self) -> ComposeResult:
        with Vertical(id="pageview"):
            yield Label(Text(self.title_text, style="bold"), id="pagehead")
            yield Static(Text("reading...", style="dim"), id="picture")
            yield Label("", id="pagefoot")

    def on_mount(self) -> None:
        self.deletes_kitty = self.picture_mode() == "kitty"
        self.show()

    def draw_graphic(self, sequence: bytes) -> None:
        """The picture goes over the (blank) area of the screen, at its top left."""
        if not self.is_attached:
            return
        region = self.query_one("#picture", Static).region
        self.place(sequence, region.x, region.y)

    def action_close(self) -> None:
        self.clear_graphic()
        self.dismiss(None)
        self.redraw_after()

    def action_turn(self, step: int) -> None:
        if self.which == "file":
            self.index = max(0, min(self.index + step, max(0, self.total - 1)))
            self.px = self.py = 0.0
            self.show()

    def action_zoom(self, step: int) -> None:
        from bdnex.ui.cover_text import ZOOMS

        at = max(0, min(len(ZOOMS) - 1, ZOOMS.index(self.zoom) + step))
        if ZOOMS[at] != self.zoom:
            self.zoom, self.px, self.py = ZOOMS[at], 0.0, 0.0
            self.show()

    def action_pan(self, across: int, down: int) -> None:
        if self.zoom > 1:
            self.px = min(1.0, max(0.0, self.px + across * self.step[0]))
            self.py = min(1.0, max(0.0, self.py + down * self.step[1]))
            self.show()

    def action_swap(self) -> None:
        self.which = "album" if self.which == "file" else "file"
        self.show()

    def show(self) -> None:
        self.token += 1
        self.run_worker(partial(self.load, self.token, self.which, self.index, self.zoom, self.px, self.py), thread=True, group="page", exclusive=True)

    def load(self, token: int, which: str, index: int, zoom: int = 1, px: float = 0.0, py: float = 0.0) -> None:
        from bdnex.importer import covers
        from bdnex.ui.cover_text import crop, picture

        path, fmt, saved = self.files
        note, total, made = "", self.total, None
        try:
            if which == "file":
                image, total = covers.page_image(path, fmt, index)
            else:
                image = saved.read_bytes() if saved is not None else None
                note = "" if image else "the album's cover is not saved (`bdnex import --fetch` saves it)"
            if image:
                size = self.app.size
                columns, rows = max(20, size.width - 2), max(8, size.height - 3)
                image, fx, fy = crop(image, zoom, px, py, columns, rows)
                self.step = (fx, fy)
                how = getattr(getattr(getattr(self.app, "settings", None), "cover", None), "picture", "symbols")
                if how in GRAPHICS:
                    sequence = graphic(image, columns, rows, how)
                    if sequence:
                        made = (Text("\n".join(" " * columns for _ in range(rows))), "graphic", sequence)
                    else:
                        note = f"{how}: chafa is not installed or failed; drawn with text instead"
                if made is None:
                    made = picture(image, columns, rows, use_chafa=how != "blocks")
            elif not note:
                note = "this page could not be read"
        except Exception as err:  # a picture is a help: say why, never end the program
            note = f"could not read the picture: {err}"
        try:
            self.app.call_from_thread(self.shown, token, which, total, made, note)
        except Exception:
            pass

    def shown(self, token: int, which: str, total: int, made, note: str) -> None:
        if token != self.token or not self.is_attached:
            return
        self.total = total
        sequence = made[2] if made and len(made) > 2 else None
        text, by = made[:2] if made else (Text(note or "(none)", style="yellow"), "")
        self.query_one("#picture", Static).update(text)
        self.clear_graphic()
        if sequence:
            self.call_after_refresh(self.draw_graphic, sequence)
        where = f"page {self.index + 1}/{total}" if which == "file" and total else "the album's saved cover"
        self.query_one("#pagefoot", Label).update(Text(
            f"the {'file' if which == 'file' else 'album'}: {where} · zoom {self.zoom}x   ·   ← → pages   ·   + - zoom (to read the writing)   ·   ↑ ↓ h l move   ·   Tab file / album   ·   Esc back"
            + ("" if by != "half blocks" else "   ·   (install chafa for a sharper picture)") + (f"   ·   {note}" if note else ""), style="dim"))


class CoverScreen(Pictures, Modal):
    """The file's first page next to the album's saved cover, drawn with text (works over SSH). Any key closes it."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("c", "close", "Close", show=False),
                Binding("q", "close", "Close", show=False), Binding("z", "zoom", "Full size"), Binding("enter", "zoom", "Full size", show=False)]
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
            yield Label(Text("z: full size (zoom with + and -, read the credits, turn the pages) · c / Esc: close", style="dim"), id="foot")

    def on_mount(self) -> None:
        self.deletes_kitty = self.picture_mode() == "kitty"
        self.run_worker(self.load, thread=True, exclusive=True)

    def load(self) -> None:
        """Both pictures drawn the way ``cover.picture`` says (the same as the full-size page): chafa symbols, half blocks, or a real picture."""
        from bdnex.ui.cover_text import GRAPHICS, graphic, picture

        how = self.picture_mode()
        left = right = None
        notes = ""
        try:
            local, online, notes = batch.read_covers(*self.files)
            columns = max(20, min(40, (self.app.size.width * 9 // 10 - 12) // 2))
            rows = max(10, min(30, self.app.size.height - 10))

            def draw(image):
                if not image:
                    return None
                if how in GRAPHICS:
                    sequence = graphic(image, columns, rows, how)
                    if sequence:
                        return (Text("\n".join(" " * columns for _ in range(rows))), "graphic", sequence)
                return picture(image, columns, rows, use_chafa=how != "blocks")

            left, right = draw(local), draw(online)
        except Exception as err:  # a picture is a help: its failure is said on the screen, never the end of the program
            left = right = None
            notes = f"could not read the pictures: {err}"
        try:
            self.app.call_from_thread(self.show_pair, left, right, notes)
        except Exception:  # the screen was closed while the thread read: nobody to show it to
            pass

    def show_pair(self, left, right, notes: str) -> None:
        if not self.is_attached or not self.query("#left"):
            return

        def side(heading: str, made) -> Text:
            text = Text(heading + "\n", style="bold")
            text.append_text(made[0] if made is not None else Text("(none)", style="dim"))
            return text

        self.query_one("#left", Static).update(side("the file", left))
        self.query_one("#right", Static).update(side("the album (saved cover)", right))
        self.clear_graphic()
        for made, where in ((left, "#left"), (right, "#right")):
            if made is not None and len(made) > 2 and made[2]:
                self.call_after_refresh(self.draw_in, made[2], where)
        if notes:
            self.query_one("#foot", Label).update(Text(notes + "   ·   z: full size · c / Esc: close", style="yellow"))

    def draw_in(self, sequence: bytes, where: str) -> None:
        if self.is_attached and self.query(where):
            region = self.query_one(where, Static).region
            self.place(sequence, region.x, region.y + 1)  # (under the heading line)

    def action_zoom(self) -> None:
        self.clear_graphic()
        self.app.push_screen(PageScreen(self.title_text, self.files))

    def action_close(self) -> None:
        self.clear_graphic()
        self.dismiss(None)
        self.redraw_after()


# ----------------------------------------------------------------------------- the table
class ReviewApp(ColumnsMixin, ReviewComicvine, ReviewActions, ReviewOrganise, ReviewExtras, App):
    """The table of one batch, the detail of the row under the cursor, and the keys that decide."""

    TITLE = "bdnex review"
    COMMANDS = App.COMMANDS | {FacetCommands}
    CSS = """
    #topbar { dock: top; height: 1; }
    #topbar Header { dock: none; width: 1fr; }
    #helpchip { width: 8; height: 1; }
    #actionschip { width: 11; height: 1; margin-right: 1; }
    #queue-badge { dock: right; width: auto; height: 1; padding: 0 1; background: red; color: white; text-style: bold; display: none; }
    #loading { height: 2fr; min-height: 8; content-align: center middle; text-align: left; }
    #main { height: 1fr; }
    #folderpane { width: 38; border-right: solid $accent; }
    #tickbar { height: 3; }
    #tickbar Button { min-width: 8; margin-right: 1; }
    #pager { height: 1; }
    #pager PagerChip { width: auto; }
    #pager-where { width: 1fr; content-align: center middle; color: $text-muted; }
    /* the two find boxes: Textual's default frame is nearly the colour of the background, so they looked like plain text */
    #treefind, #facetfind { border: tall $accent; background: $boost; }
    #treefind:focus, #facetfind:focus { border: double $success; background: $panel; }
    #treefind > .input--placeholder, #facetfind > .input--placeholder { color: $text-muted; text-style: italic; }
    #narrow { height: 1; border: none; padding: 0 1; background: transparent; }
    #trees { height: 1fr; }
    #legend { height: 2; padding: 0 1; }
    #folderslot, #moveslot, #facetslot { height: 1fr; }
    #facetbox { height: 1fr; border-top: solid $accent; }
    #foldertree, #movetree, #facettree { width: 100%; height: 100%; }
    #center { width: 1fr; }
    #table { height: 2fr; min-height: 8; }
    #detailrow { height: 1fr; min-height: 8; border-top: solid $accent; }
    #detail { width: 1fr; height: 100%; padding: 0 1; }
    #meta { width: 45%; height: 100%; border-left: solid $accent; padding: 0 1; }
    #detail-body, #meta-body { width: 100%; height: auto; }
    #tips { height: 1; padding: 0 1; color: $text-muted; background: $boost; }
    #log { height: 7; border-top: solid $accent; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [
        Binding("k", "keep", "Confirm / unconfirm"),
        Binding("u", "unlink", "Unlink"),
        Binding("t", "top", "This file's candidates"),
        Binding("o", "other", "Other album (search)"),
        Binding("O", "other_fzf", "Search with fzf", show=False),
        Binding("i", "ident", "Id / address"),
        Binding("s", "skip", "Skip"),
        Binding("l", "leave", "No album online"),
        Binding("p", "page", "Open page"),
        Binding("c", "cover", "Covers"),
        Binding("space", "mark", "Mark"),
        Binding("m", "mark_all", "Mark all / none"),
        Binding("W", "write_view", "Write the view's tags", show=False),
        Binding("M", "move_view", "Move the view's files", show=False),
        Binding("H", "repair_view", "Repair the damaged files", show=False),
        Binding("A", "actions", "Actions panel", show=False),
        Binding("delete", "delete_files", "To the trash", show=False),
        Binding("U", "undo", "Undo the last delete or move", show=False),
        Binding("x", "mark_none", "Unmark all"),
        Binding("v", "toggle_log", "Log"),
        Binding("y", "copy_log", "Copy the log"),
        Binding("a", "attach", "Attach marked to a series"),
        Binding("d", "metadata", "Album metadata panel"),
        Binding("w", "waiting", "Waiting questions"),
        Binding("e", "edit", "Edit by hand"),
        Binding("C", "columns", "Columns"),
        Binding("shift+down", "extend_mark(1)", "Mark more", show=False),
        Binding("shift+up", "extend_mark(-1)", "Mark more", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("f", "folders", "Folders on/off"),
        Binding("F", "find_folder", "Find a folder or file"),
        Binding("S", "find_filter", "Find a filter"),
        Binding("R", "reviewed_folder", "Folder reviewed"),
        Binding("T", "show_ticked", "Show the ticked"),
        Binding("D", "duplicates", "Duplicates"),
        Binding("E", "bulk", "Edit the marked files"),
        Binding("K", "kind", "Series / one-shot"),
        Binding("V", "volume", "Volume"),
        Binding("b", "previous_batch", "Previous batch"),
        Binding("n", "next_batch", "Next batch"),
        Binding("q", "quit", "Quit"),
        Binding("/", "find", "Search"),
        Binding("]", "find_next", "Next match", show=False),
        Binding("[", "find_prev", "Previous match", show=False),
        *[Binding(str(number), f"sort({number - 1})", f"Sort by {name}", show=False) for number, (name, _) in enumerate(COLUMNS[:9], start=1)],
    ]

    def __init__(self, library, settings, source, size: int = 50, terms=(), unmatched: bool = False,
                 everything: bool = False, again: bool = False, verbose: int = 0, tree: Optional[bool] = None, resume: Optional[dict] = None):
        super().__init__()
        self.resume = resume  # `--continue`: the place saved when the screen was closed (``remember_place``), shown again once the screen is up
        self.pending_sort = None
        self.tree_pref = tree  # None: as it was left last time (``f`` toggles)
        self.verbose = verbose
        self.ui_thread = None
        self.ui_loop = None
        self.fetcher = None  # the background fetching of pages and covers (`review_fetch.DetailsQueue`: downloads in parallel, one writer)
        self.first_total: Optional[int] = None
        self.unfetched = 0  # linked files whose page and cover have not come yet
        self.remaining = 0  # the same, counted by the fetching thread itself (the screen may be gone): `finish_pending_fetches` waits on it
        self.remaining_lock = threading.Lock()
        self.finish_fetches = False  # `run` sets it: closing the screen then lets the queued fetches go on (see `finish_pending_fetches`)
        self.waiting: list[Waiting] = []  # questions that came from background work: they wait here for the person (`w`), never over what is open
        self.library, self.settings = library, settings
        self.hub = SourceHub(source)
        self.batch_size, self.terms, self.unmatched = size, list(terms), unmatched
        self.everything, self.again = everything, again
        self.rows: list[batch.Row] = []
        self.album_data: dict = {}  # album id -> the album, for the cells a selection did not build
        self.file_data: dict = {}  # path -> the file's record, the same
        self.load_view()  # the columns as they were left (saved in the library)
        self.view_places: list = []  # the folders on the screen and the filters on it, for the "Apply filters to folders" box
        self.view_facets: list = []
        self.shown: set[str] = set()  # every file of every batch of this run: the next batch never repeats one
        self.number = 0
        self.terminal_stderr = sys.stderr  # the real one, before any fetch redirects it
        self.batches: list[list] = []  # every batch shown in this run, so `b` can go back to one (its rows keep their decisions)
        self.batch_totals: list[int] = []  # for each: the files left to check when it was weighed
        self.at = -1  # the batch on the screen (an index of ``batches``)
        self.ahead: Optional[tuple] = None  # the next batch, weighed while the person works on this one: (rows, seconds)
        self.ahead_running = False
        self.want_ahead = False  # `n` was pressed before the next batch was ready: it is shown as soon as it is
        self.decided: dict[str, int] = {}
        self.loading = False
        self.timing = ""
        self.sort_column: Optional[int] = None  # a column of COLUMNS, sorted by a click on its header or by a number key
        self.find_query: str = ""  # the last search typed with `/`; `]` and `[` look for the next or previous match of it
        self.sort_reverse = False
        self.marked: set[int] = set()  # ids of the rows marked with space (to attach to a series together)
        self.order: dict[int, int] = {}  # each row's place when the batch was made (``#`` sorts back to it)
        self.started = 0.0  # when the files started to be weighed
        self.weighed_in: Optional[float] = None  # how long weighing took, once it did
        self.albums_in: Optional[float] = None  # how long the album list took to load, once it did

    # -- layout
    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Header()
            yield ActionsChip()
            yield HelpChip()
        yield Static("", id="queue-badge")
        with Horizontal(id="main"):
            with Vertical(id="folderpane"):
                with Horizontal(id="tickbar"):
                    yield Button("Show ticked", id="show-ticked", tooltip="Show the files of every folder and filter you ticked (space or ctrl-click), all together.")
                    yield Button("Clear ticks", id="clear-ticks", tooltip="Untick every folder and filter.")
                yield Checkbox("Apply filters to folders (both)", id="narrow",
                               tooltip="Ticked: a click on a folder and a click on a filter work together (the files that match both). Not ticked: a click shows only what you clicked.")
                yield Input(placeholder="Find a folder or a file...", id="treefind", tooltip="Type part of a folder or file name: only the folders that match stay. Key F; Enter goes on to the tree.")
                with TabbedContent(id="trees"):
                    with TabPane("Folders", id="tab-folders"):
                        with Vertical(id="folderslot"):
                            yield Static("reading the folders...", id="foldersloading")
                    with TabPane("Organised", id="tab-moves"):
                        with Vertical(id="moveslot"):
                            yield Static("working out how `bdnex move` would organise the files...", id="movewait")
                yield Splitter("#facetbox", "y", id="split-filters", tooltip="Drag up or down to resize the trees and the filters.")
                yield Static(legend(), id="legend")
                with Vertical(id="facetbox"):
                    yield Input(placeholder="Find a filter: author, series", id="facetfind",
                                tooltip="Type part of an author, a series, a genre...: only the filter values that match stay. Key S; Enter goes on to the list.")
                    with Vertical(id="facetslot"):
                        yield Static("Filters: reading...", id="facetwait")
            yield Splitter("#folderpane", "x", id="split-left", tooltip="Drag left or right to resize the left panel.")
            with Vertical(id="center"):
                yield Static("", id="loading")
                yield ReviewTable(id="table", cursor_type="row", zebra_stripes=True)
                with Horizontal(id="pager"):
                    yield PagerChip(" ◀  b  previous ", "action_previous_batch", id="pager-back", tooltip="The previous 50 files (key b).")
                    yield Static("", id="pager-where")
                    yield PagerChip(" next  n  ▶ ", "action_next_batch", id="pager-next", tooltip="The next 50 files (key n).")
                yield Splitter("#detailrow", "y", id="split-detail", tooltip="Drag up or down to resize the album's metadata panel.")
                with Horizontal(id="detailrow"):
                    yield Pane("detail")
                    yield Pane("meta")
        yield RichLog(id="log", max_lines=500, wrap=True, markup=False, highlight=False)
        yield Static("", id="tips")
        yield Footer()

    def on_mount(self) -> None:
        import threading

        self.ui_thread = threading.get_ident()
        self.ui_loop = asyncio.get_running_loop()
        self.started = time.monotonic()
        self.take_the_log()
        self.set_interval(0.15, self.tick_pending)
        self.query_one("#meta").display = bool(self.view_extra.get("meta", True))  # the album's metadata is shown unless `d` hid it
        self.restore_layout()
        self.start_tips()
        self.load_batch()  # (the folders and the album list start once the table is there: see `start_background`)

    def on_unmount(self) -> None:
        self.give_back_the_log()
        if self.fetcher is not None and not self.finish_fetches:
            self.fetcher.shutdown(wait=False, cancel_futures=True)  # what is not fetched yet: `bdnex import --fetch`
        self.hub.close()

    def finish_pending_fetches(self, out=None) -> int:
        """After the screen is closed: wait for the pages and covers still queued (one failing, a corrupt file say, does not stop the
        others). Ctrl-C gives up on the rest. Returns how many are left for `bdnex import --fetch`."""
        out = out or self.terminal_stderr  # not `sys.stderr`: a fetch still running has it redirected into the (closed) log panel
        try:
            while True:
                with self.remaining_lock:
                    left = self.remaining
                if left <= 0 or self.fetcher is None:
                    break
                print(f"\rFetching the page and cover of {left} linked file(s)... (Ctrl-C leaves them to `bdnex import --fetch`)   ", end="", file=out, flush=True)
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        if self.fetcher is not None:
            self.fetcher.shutdown(wait=False, cancel_futures=True)
        with self.remaining_lock:
            left = max(0, self.remaining)
        if left or self.unfetched:
            print("", file=out)
        return left

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

    def tick_pending(self) -> None:
        """The seconds and the turning dots in the state column of the files being fetched: a long wait is a number going up, not a frozen screen."""
        waiting = [(index, row) for index, row in enumerate(self.rows) if row.pending or row.busy]
        if not waiting:
            return
        with contextlib.suppress(Exception):
            table = self.query_one(DataTable)  # once, and the index from the loop: not a search of the list per row
            for index, row in waiting:
                cells = self.cells(index + 1, row)
                for position in (self.view.index("link"), self.view.index("review")) if "link" in self.view and "review" in self.view else ():
                    table.update_cell_at((index, position), cells[position])

    def log_from_anywhere(self, text: str) -> None:
        import threading

        if threading.get_ident() == self.ui_thread:
            self.log_line(text)
        else:
            # never `call_from_thread`: it makes the calling thread WAIT for the screen. A background import that logs while it holds
            # the library's write lock would then wait for the screen, and the screen (writing a link) for that lock: frozen.
            try:
                self.ui_loop.call_soon_threadsafe(self.log_line, text)
            except Exception:  # the loop is closed: the screen is gone
                pass

    def log_line(self, text: str) -> None:
        stamped = f"{time.strftime('%H:%M:%S')} {text}"
        self.log_lines = [*getattr(self, "log_lines", [])[-499:], stamped]  # what `y` copies (the panel keeps 500 lines too)
        try:
            panel = self.query_one("#log", RichLog)
            # follow the new lines only while the panel is at its end: someone who scrolled up (to read, to select) is left alone
            panel.write(Text(stamped, no_wrap=False), scroll_end=panel.scroll_y >= panel.max_scroll_y - 1)
        except Exception:  # the screen is closing
            pass

    def action_copy_log(self) -> None:
        lines = getattr(self, "log_lines", [])
        if not lines:
            self.notify("the log is empty")
            return
        self.copy_to_clipboard("\n".join(lines))  # the terminal's clipboard (OSC 52), where it supports it
        self.notify(f"{len(lines)} log line(s) copied")

    def action_toggle_log(self) -> None:
        panel = self.query_one("#log", RichLog)
        panel.display = not panel.display

    background_started = False

    def start_background(self) -> None:
        """Once, as soon as the first batch is on screen (or failed): the folder tree and filters, and the album list for the search. They are
        Python work that shares the interpreter with the weighing of the first batch, so they wait for it."""
        if self.background_started:
            return
        self.background_started = True
        self.start_folders()
        self.look_for_repairs()
        self.hub.load_list().add_done_callback(self.list_loaded)
        self.remember_place(terms=list(self.terms), unmatched=self.unmatched, everything=self.everything, again=self.again)
        if self.resume:
            self.restore_place()

    def list_loaded(self, future) -> None:  # called in the thread of the album list
        self.albums_in = time.monotonic() - self.started
        logger.info("the album list is loaded (%.1f s after the start)", self.albums_in)

    # -- the loading panel: what is being done, and for how long
    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def loading_text(self) -> Text:
        now = time.monotonic()
        frame = self.SPINNER[int(now * 10) % len(self.SPINNER)]
        weighing = self.weighed_in is None
        text = Text()
        text.append(f"{frame}  " if weighing else "✓  ", style="bold cyan")
        text.append("Getting the batch ready\n\n", style="bold")
        text.append("   ")
        text.append(("✓ " if not weighing else f"{frame} "), style="green" if not weighing else "cyan")
        text.append("weighing the files: what each match says for and against, offline")
        text.append(f"   {self.weighed_in if not weighing else now - self.started:.1f} s\n", style="dim")
        text.append("\nThe table opens as soon as the files are weighed. The folders, the filters and the album list (for the search) load right after, "
                    "in the background.", style="dim")
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
        self.query_one("#detail", Pane).update(Text(text, style=style))

    def load_batch(self) -> None:
        if self.loading:
            return
        self.number = len(self.batches) + 1
        if self.ahead is not None:  # weighed meanwhile: nothing to wait for
            rows, seconds = self.ahead
            self.ahead = None
            self.show_batch(rows, seconds)
            return
        self.loading = True
        if self.batches and self.at < len(self.batches):
            self.batches[self.at] = self.rows  # kept as it is: the table is emptied for the new one, and a failure must give it back
        self.rows = []
        self.query_one(DataTable).clear()
        self.sub_title = f"batch {self.batch_of()} · getting ready..."
        self.say("")
        self.started, self.weighed_in = time.monotonic(), None
        self.show_loading(True)
        if self.ahead_running:  # on its way: shown by `prepared` when it lands
            self.want_ahead = True
            return
        self.run_worker(self.weigh, thread=True, exclusive=True, group="batch")

    def prefetch(self) -> None:
        """Weigh the next batch in the background while this one is looked at (a daemon thread with its own read-only library:
        quitting never waits for it). ``shown`` already holds this batch's files, so it never repeats them."""
        if self.ahead is not None or self.ahead_running or not self.rows or self.batch_total <= len(self.rows):
            return  # already there, on its way, or these were all the files
        self.ahead_running = True
        threading.Thread(target=self.prepare, args=(set(self.shown),), name="bdnex-next-batch", daemon=True).start()

    def prepare(self, skip: set) -> None:
        from bdnex.library import Library

        started = time.monotonic()
        rows = None
        try:
            with Library.for_settings(self.settings, readonly=True) as library:
                rows = batch.next_batch(library, self.settings, self.batch_size, self.terms, self.unmatched, self.everything, self.again, skip)
        except Exception as err:
            logger.info("the next batch could not be prepared ahead: %s", err)
        try:
            self.ui_loop.call_soon_threadsafe(self.prepared, rows, time.monotonic() - started)
        except Exception:  # the loop is closed: the screen is gone
            pass

    def prepared(self, rows, seconds: float) -> None:
        self.ahead_running = False
        if rows is None:  # not weighed ahead: `n` weighs it as before
            if self.want_ahead:
                self.want_ahead = False
                self.run_worker(self.weigh, thread=True, exclusive=True, group="batch")
            return
        if self.want_ahead:
            self.want_ahead = False
            self.show_batch(rows, seconds)
        else:
            self.ahead = (rows, seconds)

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
        self.sub_title = f"batch {self.batch_of()}"
        if self.batches and self.at < len(self.batches):
            self.display(self.batches[self.at], "seen before")  # back to the batch that was there (`self.rows` was emptied for the new one)
        self.say(f"Could not weigh the files: {message}", "red")
        self.start_background()

    def show_batch(self, rows: list[batch.Row], seconds: float) -> None:
        """A new batch: kept in ``batches`` (to come back to), shown, and the one after it is weighed in the background."""
        self.batches.append(rows)
        self.batch_totals.append(getattr(rows, "total", len(rows)))
        self.at = len(self.batches) - 1
        self.number = self.at + 1
        self.display(rows, f"weighed in {seconds:.1f} s")
        self.call_after_refresh(self.start_background)  # (after the table was drawn)
        self.prefetch()

    def display(self, rows: list[batch.Row], timing: str) -> None:
        self.loading = False
        self.show_loading(False)
        self.query_one(DataTable).focus()
        if self.first_total is None:
            self.first_total = getattr(rows, "total", 0)  # the files to check when the run began: the batches there will be about
        self.rows = rows
        self.batch_total = self.batch_totals[self.at]  # the files to check when this batch was weighed: more than the rows = another batch
        self.shown.update(row.path for row in rows)
        self.marked.clear()
        self.order = {id(row): place for place, row in enumerate(rows)}
        self.sort_column, self.sort_reverse = None, False
        self.timing = timing
        self.render_table()
        self.update_pager()
        if not self.rows:
            self.sub_title = "nothing left to check" + self.progress_text()
            self.say("Nothing left to check: every doubtful file has been looked at. "
                     "`bdnex import --skipped --auto=all` decides the unlinked ones again.", "green")

    def update_pager(self) -> None:
        """The bar under the table: where you are in the pages (or batches) and the two arrows that turn them (`b` and `n` do the same)."""
        selection = getattr(self.rows, "selection", None)
        if selection:
            total = getattr(self.rows, "total", len(self.rows))
            pages = max(1, -(-total // self.batch_size))
            page = selection[2]
            where = f"page {page + 1} of {pages} · {total} files"
            more_back, more_next = page > 0 or self.at > 0, page + 1 < pages or self.at < len(self.batches) - 1
        else:
            where = f"batch {self.at + 1}"
            more_back, more_next = self.at > 0, True
        with contextlib.suppress(Exception):
            self.query_one("#pager-where", Static).update(where)
            self.query_one("#pager-back").display = more_back
            self.query_one("#pager-next").display = more_next

    def table_width(self) -> int:
        """The room the table has: the screen less the folder tree, when it is shown."""
        with contextlib.suppress(Exception):
            return self.query_one("#center").size.width or self.size.width
        return self.size.width

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
        if getattr(self, "double_header", False):  # a double-click opens the column's field chooser: it is not a sort
            self.double_header = False
            return
        self.action_sort(event.column_index)

    def action_sort(self, index: int) -> None:
        """Sort by the column ``index``; the same column again reverses it. ``#`` is the order of the batch."""
        if not (0 <= index < len(self.view)) or not self.rows:
            return
        self.sort_reverse = (not self.sort_reverse) if index == self.sort_column else False
        self.sort_column = index
        self.remember_place(sort=[self.view[index], self.sort_reverse])
        keep = self.current()
        keys = [(self.sort_key(index, row), row) for row in self.rows]
        present = sorted((pair for pair in keys if pair[0] is not None), key=lambda pair: pair[0], reverse=self.sort_reverse)
        sorted_rows = batch.Batch([row for _, row in present] + [row for key, row in keys if key is None])  # without a value: last
        sorted_rows.__dict__.update(getattr(self.rows, "__dict__", {}))  # (the total, the page of a selection)
        self.rows = sorted_rows
        self.batches[self.at] = self.rows
        self.render_table()
        if keep is not None:
            self.query_one(DataTable).move_cursor(row=self.rows.index(keep))

    def update_title(self) -> None:
        done = sum(1 for row in self.rows if row.status)
        by = f" · by {COLUMN_TYPES[self.view[self.sort_column]][0].lower()} {'▼' if self.sort_reverse else '▲'}" if self.sort_column is not None else ""
        self.sub_title = f"batch {self.batch_of()} · {done}/{len(self.rows)} checked{by} · {self.timing}".rstrip(" ·") + self.progress_text()
        self.schedule_counts()

    def batch_of(self) -> str:
        """``3/53``: this batch and about how many there are (files to check when the run began, by the batch size)."""
        if not self.first_total:
            return str(self.number)
        return f"{self.number}/{max(self.number, -(-self.first_total // self.batch_size))}"

    LABEL_WIDTH = 9

    def detail_text(self, row: batch.Row) -> Text:
        """The pane under the table: one block per thing (the file, the album it is linked to, what bdnex thinks, where a move would put it),
        each with its label in the same column, so the eye finds the block before it reads."""
        text = Text()
        pad = " " * self.LABEL_WIDTH

        def block(label: str, style: str = "bold cyan") -> None:
            text.append(f"{label:<{self.LABEL_WIDTH}}", style=style)

        def more(value, style: str = "") -> None:  # a further line of the same block (the pad is styled: an unstyled blank is dropped when drawn)
            text.append(pad, style="dim")
            text.append(value if isinstance(value, Text) else Text(str(value), style=style))
            text.append("\n")

        album_facts = [line for line in (row.facts or "").split("\n") if line and not line.startswith("file:")]
        file_facts = [line[len("file:"):].strip() for line in (row.facts or "").split("\n") if line.startswith("file:")]
        folder, name = os.path.split(row.path)
        block("FILE")
        text.append(name + "\n", style="bold")
        if folder:
            more(folder, "dim")
        for line in file_facts:
            more(line, "cyan")

        block("ALBUM", "bold magenta")
        text.append_text(album_text(row))
        text.append("   ")
        text.append(f" {KIND_WORD.get(row.kind, row.kind)} ", style=f"bold black on {KIND_COLOUR.get(row.kind, 'white')}")
        text.append("\n")
        for line in album_facts:  # the year and pages on one line, the authors on the next (the other credits are in the metadata panel, `d`)
            parts = line.split(" · ")
            plain = " · ".join(part for part in parts if ": " not in part)
            if plain:
                more(plain, "")
            authors = []
            for part in parts:
                role, _, names = part.partition(": ")
                if names and role in ("Writer", "Scénario", "Penciller", "Artist", "Dessin"):
                    authors += [n for n in names.split("; ") if n not in authors]
            if authors:
                more(Text.assemble(("by ", "dim"), "  ·  ".join(authors[:4])))
        if row.album_url:  # a link: ctrl/cmd-click in most terminals, or `p`
            more(Text(row.album_url, style=Style(color="cyan", underline=True, link=row.album_url)))
        if row.why:
            block("WHY", "bold yellow")
            first = True
            for line in row.why[:5]:
                if not first:
                    text.append(pad, style="dim")
                first = False
                text.append(line + "\n", style=EVIDENCE_STYLE.get(line[:1], ""))
        if row.dest and row.dest != "?":
            block("GOES TO", "bold green")  # where `bdnex move` would put it (the template of your config): folder, then the new name
            where, new_name = os.path.split(row.dest)
            text.append((where + "/" if where else "") + "\n", style="dim")
            more(new_name, "bold")
        if row.message:
            block("NOW", "bold yellow")
            text.append(row.message + "\n", style="bold yellow" if row.busy else "yellow")
        return text

    def show_detail(self, index: int) -> None:
        if not (0 <= index < len(self.rows)):
            return
        row = self.rows[index]
        self.query_one("#detail", Pane).update(self.detail_text(row))
        self.show_meta(row)

    # -- the metadata the library holds for the album of the file under the cursor (`d`), which `e` can change
    def action_metadata(self) -> None:
        panel = self.query_one("#meta", Pane)
        panel.display = not panel.display
        self.remember(meta=panel.display)
        row = self.current()
        if panel.display and row is not None:
            self.show_meta(row)

    def show_meta(self, row: batch.Row) -> None:
        panel = self.query_one("#meta", Pane)
        if not panel.display:
            return
        file = self.library.get_file(row.path) or {}
        album = self.library.get_album(file["album_id"]) if file.get("album_id") is not None else None
        text = Text()
        if album is None:
            text.append("No album: the file has no metadata of its own to show.\n`o` searches one, `e` writes one by hand.", style="dim")
            panel.update(text)
            return
        edits = (album.get("extra") or {}).get("edits") or {}

        def line(name: str, value) -> None:
            if value in (None, "", [], {}):
                return
            text.append(f"{name:<12}", style="dim")
            text.append(f"{value}", style="bold yellow" if name.strip().lower() in {k.lower() for k in edits} else "")
            text.append("\n")

        def heading(word: str, colour: str) -> None:
            if text.plain and not text.plain.endswith("\n\n"):
                text.append("\n")
            text.append(f" {word} ", style=f"bold black on {colour}")
            text.append("\n")

        text.append(album["title"] + "\n", style="bold underline")
        heading("ALBUM", "magenta")
        line("series", album.get("series"))
        line("volume", album.get("volume") if album.get("volume") is not None else album.get("alternate_number"))
        date = "-".join(f"{album[k]:02d}" if k != "year" else str(album[k]) for k in ("year", "month", "day") if album.get(k))
        line("date", date)
        credits = album.get("credits") or {}
        if credits or album.get("genres"):
            heading("PEOPLE", "cyan")
            for role, names in credits.items():
                line(role.lower(), "; ".join(names))
            line("genres", "; ".join(album.get("genres") or []))
        heading("EDITION", "yellow")
        line("publisher", album.get("publisher"))
        line("language", album.get("language"))
        line("pages", album.get("pages"))
        line("isbn", album.get("isbn"))
        line("source", f"{album.get('source')} {album.get('source_id')}")
        if album.get("summary"):
            heading("SUMMARY", "green")
            text.append(album["summary"] + "\n", style="italic")
        if edits:
            text.append("\nchanged by you (kept when the album is read again): " + ", ".join(sorted(edits)) + "\n", style="yellow")
        text.append("\ne edits these · d closes", style="dim")
        panel.update(text)

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

    # -- search (`/`): a quick jump by text, not a filter (every row stays on the screen)
    def row_haystack(self, row: batch.Row) -> str:
        words = (os.path.basename(row.path), row.folder, row.album or "no album", row.dest,
                 row.status, KIND_WORD.get(row.kind, row.kind))
        return " ".join(words).lower()

    def find_match(self, start: int, step: int) -> Optional[int]:
        """The row closest to ``start`` (going by ``step``, wrapping around) whose file, folder, album, destination or state holds
        ``self.find_query``; ``None`` when nothing does, or there is nothing to look for."""
        query = self.find_query.strip().lower()
        if not query or not self.rows:
            return None
        count = len(self.rows)
        for offset in range(count):
            index = (start + offset * step) % count
            if query in self.row_haystack(self.rows[index]):
                return index
        return None

    def action_find(self) -> None:
        self.push_screen(AskScreen("Search (file, folder, album, destination, state):", self.find_query), self.find_submitted)

    def find_submitted(self, text: Optional[str]) -> None:
        if text is None:  # Esc: the search in progress (if any) is unchanged
            return
        self.find_query = text.strip()
        self.find_step(1, include_current=True)

    def action_find_next(self) -> None:
        self.find_step(1)

    def action_find_prev(self) -> None:
        self.find_step(-1)

    def find_step(self, step: int, include_current: bool = False) -> None:
        """Move to the next (or, ``step=-1``, previous) row matching the last search. ``]``/`[` repeat it without opening the search box."""
        if not self.find_query:
            self.notify("No search yet: press / first.", severity="warning")
            return
        table = self.query_one(DataTable)
        start = table.cursor_row if include_current else table.cursor_row + step
        index = self.find_match(start, step)
        if index is None:
            self.notify(f"No match for {self.find_query!r}.", severity="warning")
        else:
            table.move_cursor(row=index)

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
            if self.at < len(self.batches) - 1:  # an earlier batch: the next one is known, `n` shows it
                self.notify("This batch is checked: n goes on to the next one.", timeout=4)
                return
            if getattr(self, "batch_total", 0) <= len(self.rows):  # these were all the files there are: nothing to ask about
                self.sub_title = "everything checked"
                self.say("Everything is checked. Press q to quit: the pages still being fetched are finished first.", "green")
                self.notify("Everything is checked: q quits", timeout=6)
                return
            self.ask_later(f"batch {self.number} is done: check the next one?",
                           lambda: AskScreen(f"Batch {self.number} is done. Check the next batch?", None), self.continue_with)

    def continue_with(self, answer) -> None:
        if answer:
            self.load_batch()
        elif answer is False:
            self.exit()

    # -- decisions: each one is saved at once
    def supersede(self, row: batch.Row) -> None:
        """A newer decision for the row: the page and cover still queued for the older link are cancelled (their job sees `link_id` changed)
        and the row is no longer waiting for them (it would stay 'fetching' for ever: that job ends without telling it)."""
        row.link_id += 1
        row.pending = False
        row.dest = ""  # the album (so the place) is about to change: no destination is better than the old one

    def refresh_dest(self, row: batch.Row) -> None:
        """Where a move would put the file NOW: the album it has in the library (a decision, a link, an import that ended). `supersede` clears it
        when a decision starts; this puts the new one back. Offline, a few template lookups: never the reason a decision fails."""
        with contextlib.suppress(Exception):
            file = self.library.get_file(row.path) or {}
            row.dest = batch.destination_of(self.library, self.settings, file, file.get("album_id"))
            self.forget_cached()  # (its album may have changed kind or volume)
            row.album_kind = ""
            if file.get("album_id") is not None:
                self.plan_dirty.add(file["album_id"])  # the second tree follows: where a move would put this album's files is worked out again

    def record(self, action: str) -> None:
        row = self.idle()
        if row is None:
            return
        self.supersede(row)
        try:
            batch.decide(self.library, row, action)
        except Exception as err:  # a locked library: said, and the row stays as it was
            logger.warning("%s: decision not saved: %s", os.path.basename(row.path), err, exc_info=True)
            self.notify(f"Not saved ({err}): try again", severity="error", timeout=8)
            return
        self.refresh_dest(row)
        self.decided[row.status] = self.decided.get(row.status, 0) + 1
        self.refresh_row(row)
        self.advance()

    def action_keep(self) -> None:
        """Confirm the file (the match is right: it turns green), or take the confirmation back when it is confirmed already. With files marked
        (space), all of them: the confirmed ones are unconfirmed only when every marked file is confirmed. A file with no album is confirmed as
        having no album online."""
        rows = [r for r in self.rows if id(r) in self.marked and not r.busy] or ([self.idle()] if self.idle() is not None else [])
        if not rows:
            return
        if all(batch.is_confirmed(r) for r in rows):
            for row in rows:
                self.supersede(row)
                batch.unconfirm(self.library, row)
                self.refresh_row(row)
            self.notify(f"{len(rows)} file(s) not confirmed any more", timeout=3)
            self.schedule_counts()
            return
        todo = [r for r in rows if not batch.is_confirmed(r)]
        for row in todo:
            self.supersede(row)
            try:
                batch.decide(self.library, row, "keep" if row.album_id is not None else "leave")
            except Exception as err:
                logger.warning("%s: decision not saved: %s", os.path.basename(row.path), err, exc_info=True)
                self.notify(f"Not saved ({err}): try again", severity="error", timeout=8)
                return
            self.refresh_dest(row)
            self.decided[row.status] = self.decided.get(row.status, 0) + 1
            self.marked.discard(id(row))
            self.refresh_row(row)
        if len(todo) == 1:
            self.advance()
        else:
            self.notify(f"{len(todo)} file(s) confirmed", timeout=3)

    def action_unlink(self) -> None:
        row = self.current()
        if row is not None and row.album_id is not None:
            self.record("unlink")
        elif row is not None:
            self.notify("This file has no album to unlink.", severity="warning")

    def action_leave(self) -> None:
        """No album online: you looked, nothing exists for the file, so it stays without an album, and is confirmed."""
        row = self.current()
        if row is not None and row.album_id is not None:
            self.notify("This file has an album: `k` confirms it, `u` unlinks it first.", severity="warning")
            return
        self.record("leave")

    def action_skip(self) -> None:
        row = self.idle()
        if row is not None:
            if row.status not in ("", "skipped"):  # decided already (and saved): skipping would only change what the table says
                self.notify("This file is decided already: `k` confirms (again: unconfirms), `u` unlinks, `l` says there is no album online, `o` picks another album.", severity="warning")
                return
            row.status = "skipped"  # nothing is saved: it is in the next batch again
            self.refresh_row(row)
            self.advance()

    # -- several files at once: mark them (space), then give them the series of an album
    def action_mark(self) -> None:
        row = self.current()
        if row is None or row.busy:
            return
        self.marked.symmetric_difference_update({id(row)})
        self.mark_anchor = self.query_one(DataTable).cursor_row  # (a shift-click marks from here)
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
        if len(marked) >= 2:  # several files: the form that shows what they share and what differs (never one file's name for all of them)
            self.action_bulk()
            return
        file = self.library.get_file(row.path) or {}
        album_id = file.get("album_id")
        if album_id is not None and not manual.is_manual(self.library, album_id):
            self.edit_metadata(row, album_id)  # an album of the site: its fields can be changed (kept when it is read again), not rebuilt
            return
        values = manual.form_of(self.library, album_id) if album_id is not None else {**manual.guess(row.path), "title": ""}
        versions = self.settings.duplicates.version_labels if album_id is not None else None  # (a file with no album has nothing to be a version of)
        self.push_screen(EditScreen(f"Metadata of {os.path.basename(row.path)}", values, manual.FIELDS, versions=versions),
                         lambda got: self.save_one(row, album_id, got) if got else None)

    def edit_metadata(self, row: batch.Row, album_id: int) -> None:
        """The metadata the library holds for the album of the file, in the form of a hand-written album. What changes is saved as the person's
        own edits (`bdnex edit`): a later import or `--fetch --force` does not undo them. The series name can be changed too: it renames the
        series of EVERY album of it, so the person is asked first (and told how many)."""
        from bdnex.library import manual

        values = manual.form_of(self.library, album_id)
        in_series = (self.library.get_album(album_id) or {}).get("kind") == "series"
        fields = list(manual.FIELDS) if in_series else [f for f in manual.FIELDS if f[0] != "series"]  # a one-shot has no series
        heading = f"Metadata of {values.get('title') or 'the album'} (saved as your own edits)"
        if in_series:
            heading += " · the series name renames it for all its albums"
        originals = manual.site_form(self.settings, self.library, album_id) or {}
        self.push_screen(EditScreen(heading, values, fields, originals=originals, versions=self.settings.duplicates.version_labels),
                         lambda got: self.save_metadata(row, album_id, got) if got else None)

    def save_metadata(self, row: batch.Row, album_id: int, values: dict) -> None:
        album = self.library.get_album(album_id) or {}
        values = dict(values)
        old, new = album.get("series") or "", (values.get("series") or "").strip()
        if album.get("kind") != "series":
            values = {**values, "series": old}  # a one-shot has none
        elif not new:
            self.notify("A series needs its name (to remove the album from it, unlink it).", severity="error")
            return
        elif new != old:
            count = len(self.library.series_albums(album_id))
            if count > 1:  # it is the series of every one of them that changes: said, and asked, before it is done
                self.push_screen(
                    AskScreen(f"Rename the series {old!r} to {new!r}?\nIt changes all {count} albums of that series, and where a move would put "
                              "their files.", None),
                    lambda yes: self.apply_metadata(row, album_id, values if yes else {**values, "series": old}))
                return
        self.apply_metadata(row, album_id, values)

    def apply_metadata(self, row: batch.Row, album_id: int, values: dict) -> None:
        from bdnex.library import manual

        back = values.pop("__restore__", [])
        version = values.pop("__version__", "")
        try:
            manual.update(self.library, album_id, values)
            if back:
                manual.forget_edits(self.library, album_id, back)
        except manual.FormError as err:
            self.notify(str(err), severity="error")
            return
        except Exception as err:  # a locked library: said, nothing was half written (one transaction)
            self.notify(f"Not saved ({err}): try again", severity="error", timeout=8)
            return
        self.refresh_album_rows(set(self.library.series_albums(album_id)) | {album_id})  # (a renamed series changes every album of it)
        if version:
            self.make_versions([row], version)
        self.show_meta(row)
        logger.info("%s: metadata of the album %s changed by hand", os.path.basename(row.path), album_id)
        self.notify("Saved: kept when the album is read again. `bdnex write` puts it in the file.", timeout=6)

    def refresh_album_rows(self, album_ids: set) -> None:
        """The rows of this batch whose file has one of these albums: their label, facts and destination are read again (a title, a volume or a
        series name changed), so the table says what the library holds."""
        from bdnex.ui.describe import label

        self.plan_dirty.update(album_ids)
        for other in self.rows:
            file = self.library.get_file(other.path) or {}
            if file.get("album_id") in album_ids:
                album = self.library.get_album(file["album_id"]) or {}
                other.album, other.facts = label(album), batch.facts_of(album, file)
                self.refresh_dest(other)  # the name a move would give
                self.refresh_row(other)

    def edit_series(self, rows: list, series: str = "") -> None:
        """The form that writes one series by hand for these files: the volume of each is read from its name, the series name is the folder's
        (or ``series``, when the person was offered this after a series page that did not fit)."""
        from bdnex.library import manual

        files = [(r.path, manual.guess(r.path)["volume"]) for r in rows]
        values = {**manual.guess(rows[0].path), "volume": ""}
        if series:
            values["series"] = series
        fields = [f for f in manual.FIELDS if f[0] in manual.SHARED]
        heading = f"A series for these {len(rows)} files (one album each, with the volume in its name)"
        self.push_screen(EditScreen(heading, values, fields, [(p, int(v) if v else None) for p, v in files]),
                         lambda got: self.save_series(rows, got) if got else None)

    def save_one(self, row: batch.Row, album_id, values: dict) -> None:
        from bdnex.library import manual

        values = dict(values)
        version = values.pop("__version__", "")
        try:
            if album_id is not None:
                manual.update(self.library, album_id, values)
            else:
                album_id = manual.create(self.library, row.path, values)
        except manual.FormError as err:
            self.notify(str(err), severity="error")
            return
        self.supersede(row)  # the album of the site queued before is not to be linked back over this one
        try:
            batch.record_manual(self.library, row, album_id)
        except Exception as err:
            self.link_failed(row, err)
            return
        if version:
            self.make_versions([row], version)
        self.refresh_dest(row)
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
            self.supersede(row)
            try:
                batch.record_manual(self.library, row, album_id)
            except Exception as err:
                self.link_failed(row, err)
                continue
            self.refresh_dest(row)
            self.marked.discard(id(row))
            self.refresh_row(row)
        self.decided["linked"] = self.decided.get("linked", 0) + len(rows)
        logger.info("%d file(s) written by hand as the series %s", len(rows), values.get("series"))
        self.notify(f"{len(rows)} file(s) linked to the series {values.get('series')!r} (written by hand)", timeout=6)
        self.advance()

    def action_mark_all(self) -> None:
        """Mark every file on the screen (decided or not: a folder you are editing is full of decided ones; only a file being linked is left out);
        when they are all marked already, unmark them."""
        free = [r for r in self.rows if not r.busy]
        if not free:
            self.notify("Nothing to mark here.", timeout=3)
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

    @staticmethod
    def about_files(rows: list, shown: int = 4) -> list[str]:
        """``folder/file name`` of each file (the first few): what the search is for, on the search screen."""
        lines = [f"{row.folder}/{os.path.basename(row.path)}" if row.folder else os.path.basename(row.path) for row in rows[:shown]]
        if len(rows) > shown:
            lines.append(f"... and {len(rows) - shown} more")
        return lines

    def action_attach(self) -> None:
        """Pick an album of a series: each marked file (without a mark: the file under the cursor) gets the album of that series with
        the volume number in its name. `m` marks every file on the screen, for a whole folder of one series."""
        chosen = self.files_to_attach()
        if not chosen:
            return
        if len(chosen) == 1:
            heading = ("Pick this file's album, or a SERIES to give it its volume by the number in its name "
                       "(or paste the address of a Bédéthèque series page, or a Comic Vine volume)")
        else:
            heading = f"These {len(chosen)} marked files: pick a SERIES (or an album of it, or the address of a Bédéthèque series page or a Comic Vine volume): each gets its volume"
        self.push_screen(FuzzyScreen(self.hub, self.search_start(chosen[0]), heading, series_ok=True, about=self.about_files(chosen)),
                         lambda candidate: self.run_worker(self.guarded(self.propose(chosen, candidate), "the series")) if candidate is not None else None)

    def files_to_attach(self) -> list:
        row = self.current()
        if row is None:
            return []
        chosen = [r for r in self.rows if id(r) in self.marked and not r.busy]
        if not chosen and not row.busy:
            chosen = [row]  # nothing marked: the file under the cursor, never "the rest of its folder" (one-shots share a folder)
        if not chosen:
            self.notify("Still linking this file: wait for it to finish.", severity="warning")
        return chosen

    # -- questions that come by themselves: queued, never put over what the person is doing
    def ask_later(self, title: str, make, callback) -> None:
        """A question that arrives from background work (an answer to `a`, a finished batch): it waits in a queue, with a red mark at the top right
        and a note, until the person presses `w`. Popping it up would cover the form they are filling (two windows over each other, one of
        them answered by a key meant for the other)."""
        self.waiting.append(Waiting(title, make, callback))
        self.show_waiting()
        self.notify(f"A question is waiting: {title}  (w opens it)", timeout=6)

    def show_waiting(self) -> None:
        with contextlib.suppress(Exception):
            badge = self.query_one("#queue-badge", Static)
            count = len(self.waiting)
            badge.update(f"● {count} question{'s' if count != 1 else ''} waiting · w" if count else "")
            badge.display = bool(count)

    def action_waiting(self) -> None:
        if not self.waiting:
            self.notify("No question is waiting.", timeout=3)
            return
        if len(self.screen_stack) > 1:  # a window is open: it is finished first, the question stays where it is
            self.notify("Finish the open window first: the question waits.", severity="warning", timeout=4)
            return
        item = self.waiting.pop(0)
        self.show_waiting()
        self.push_screen(item.make(), item.callback)

    async def guarded(self, work, what: str = "that") -> None:
        """Run a worker's coroutine so that its failure is said and logged: Textual ends the whole program for a worker that raises
        (the screen, the pending fetches and what was typed would be gone)."""
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.exception("%s failed", what)
            self.notify(f"Could not do {what}: {err}", severity="error", timeout=10)

    READING = "reading the series"

    async def propose(self, chosen: list, candidate) -> None:
        """``candidate``: an album of the series, or the address (text) of the series page.

        Reading a series page can take seconds (it is fetched once): the marked files say so in their state column, with the turning dots and
        the seconds, until the answer is here (or it failed), and meanwhile they can not be decided again."""
        quick = len(chosen) == 1 and not isinstance(candidate, str)  # one file, one album: linked at once, nothing to wait for
        waiting = [] if quick else [r for r in chosen if not r.busy]
        now = time.monotonic()
        for row in waiting:
            row.busy, row.busy_what, row.started_at = True, self.READING, now
            self.refresh_row(row)
        try:
            await self.propose_for(chosen, candidate)
        finally:
            for row in waiting:
                if row.busy_what == self.READING:  # (not a state somebody else gave it meanwhile)
                    row.busy, row.busy_what, row.started_at = False, "", 0.0
                    if row in self.rows:
                        self.refresh_row(row)

    async def propose_for(self, chosen: list, candidate) -> None:
        from bdnex.importer import comicvine

        paths = [r.path for r in chosen]
        if len(chosen) == 1 and not isinstance(candidate, str) and not chosen[0].busy:
            # one file and one album picked: that album is the answer. Looking up "the volume of the file in the album's series" would
            # second-guess the pick with a number read from the name (`CYCLE 2 - Tome 01` reads as 2): a SERIES pick is what does that.
            self.link(chosen[0], candidate.url, candidate)
            return
        if isinstance(candidate, str) and comicvine.is_address(candidate):
            await self.comicvine_address(chosen, candidate)
            return
        if isinstance(candidate, str):
            self.notify("Reading the series page (from the library, or fetched once)...", timeout=6)
            try:
                page = await asyncio.to_thread(batch.read_series_address, self.settings, candidate)  # may fetch: not in the screen's thread
                found = await asyncio.wrap_future(self.hub.run(
                    lambda hub: batch.propose_for_series_address(hub.source, candidate, paths, found=page)))
            except batch.NoVolumeFound as err:
                logger.info("%s", err)
                series_name = err.name  # `err` does not outlive this block: the answer below is given later
                if err.none_matched and len(chosen) >= 2:  # not this series: say so, and offer the way out (a series written by hand)
                    text = (f"{err}\n\nWrite these {len(chosen)} files by hand as the series {series_name!r}? "
                            "(the form opens with each volume read from its file name)")
                    self.ask_later(f"write {len(chosen)} files by hand as {series_name!r}?", lambda: AskScreen(text, None),
                                   lambda yes: self.edit_series(chosen, series_name) if yes else None)
                else:
                    self.notify(str(err), severity="warning", timeout=12)
                return
            except Exception as err:
                self.notify(f"Could not read that series page: {err}", severity="error")
                return
        else:
            found = await asyncio.wrap_future(self.hub.run(lambda hub: batch.propose_for_series(hub.source, candidate, paths)))
        if found is None:
            names = ", ".join(os.path.basename(path) for path in paths[:3])
            logger.info("attach: no volume found for %s (the album is in no series, or no file name has a volume number)", names)
            if len(chosen) == 1 and not isinstance(candidate, str) and not chosen[0].busy and not chosen[0].status:
                # a one-shot (or a file without a volume number): there is nothing to look up, the album picked is the answer
                self.link(chosen[0], candidate.url, candidate)
                return
            self.notify("That album is not in a series, or no file name has a volume number (T03, Tome 3...). "
                        "To link one file to one album, use `o`.", severity="warning", timeout=10)
            return
        pairs = [(r, found.mapping[r.path]) for r in chosen if r.path in found.mapping]
        left = len(chosen) - len(pairs)
        question = (f"{found.name}: {len(pairs)} of {len(chosen)} file(s) find their volume"
                    + (f" ({left} left as they are)" if left else "") + ". Check each album; change one with Enter.")
        every = [(r, found.mapping.get(r.path)) for r in chosen]  # the files with no volume are listed too: they can be matched by hand
        self.ask_later(f"link {len(pairs)} file(s) to {found.name}?", lambda: VolumesScreen(question, every, self.hub, found.name),
                       lambda picked: self.link_many(picked) if picked else None)

    def link_many(self, pairs: list) -> None:
        """Link each file to its album at once (a write in the library); the pages and covers come in the background, one by one."""
        pairs = [(row, candidate) for row, candidate in pairs if not row.status and not row.busy]  # decided while the question was open
        for row, candidate in pairs:
            self.link(row, candidate.url, candidate, notify=False, advance=False)
        self.notify(f"{len(pairs)} file(s) linked; their pages and covers are fetched in the background", timeout=6)
        self.advance()

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
        if not open_in_browser(row.album_url):
            show_address(self, row.album_url)

    # -- another album
    def search_start(self, row: batch.Row) -> str:
        from bdnex.importer.matching import query_from_filename

        return query_from_filename(row.path)

    def action_top(self) -> None:
        """The few albums this file could be, with the evidence (the list of `bdnex candidates`): quick, and one Enter links the right one."""
        row = self.idle()
        if row is None:
            return

        def picked(answer) -> None:
            if answer == "search":
                self.action_other()
            elif answer is not None:
                self.link(row, answer.url, answer)

        self.push_screen(CandidatesScreen(self.hub, row.path, row.album_id, f"Which album is {row.folder}/{os.path.basename(row.path)}?"), picked)

    def action_other(self) -> None:
        """Another album: a search box over the whole album list, filtered as you type (the fuzzy search of ``fzf``)."""
        row = self.idle()
        if row is None:
            return
        marked = [r for r in self.rows if id(r) in self.marked and not r.busy]
        heading = "Which album is this file?"
        if len(marked) > 1:  # an album is picked for the file under the cursor; a Comic Vine volume (F4) is for all the marked files
            heading += f"  (F4, the Comic Vine search, is for the {len(marked)} marked files)"
        self.push_screen(FuzzyScreen(self.hub, self.search_start(row), heading, about=self.about_files(marked if len(marked) > 1 else [row])),
                         lambda candidate: self.other_picked(row, candidate))

    def other_picked(self, row, candidate) -> None:
        if candidate is None:
            return
        if isinstance(candidate, str):  # a Comic Vine volume, found by its search (F4): each marked file gets the issue with its number
            chosen = [r for r in self.rows if id(r) in self.marked and not r.busy] or [row]
            self.run_worker(self.guarded(self.comicvine_address(chosen, candidate), "that volume"))
            return
        self.link(row, candidate.url, candidate)

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
            from bdnex.importer import bdtheque, comicvine

            if comicvine.is_address(text):  # Comic Vine: an issue is this file's album, a volume gives each marked file its issue
                chosen = self.files_to_attach() if comicvine.parse(text)[0] == "volume" else [row]
                if chosen:
                    await self.comicvine_address(chosen, text)
                return
            if bdtheque.is_address(text):  # another site: its page is read (fetched once) and the file is linked to what it says
                row.busy, row.busy_what, row.started_at = True, "fetching the page", time.monotonic()  # the state column says so, and counts
                self.refresh_row(row)
                try:
                    info = await asyncio.to_thread(batch.read_bdtheque, self.settings, text.strip(), row.path)
                except Exception as err:
                    self.notify(f"Could not read that page: {err}", severity="error", timeout=10)
                    return
                finally:
                    row.busy, row.busy_what, row.started_at = False, "", 0.0
                    self.refresh_row(row)
                self.link_page(row, info)
                return
            candidate = await asyncio.wrap_future(self.hub.run(lambda hub: hub.source.candidate_for(text)))
            if candidate is None:
                self.notify(f"{text!r} is not an album: give the address of its page, or its number.", severity="error")
                return
            self.link(row, candidate.url, candidate)

        self.push_screen(AskScreen("The address of a page: a Bédéthèque album, a bdtheque.com series, a Comic Vine issue (or volume), or an album number (Bédéthèque):", ""),
                         lambda text: self.run_worker(self.guarded(given(text), "that address")) if text else None)

    # -- linking: the match is written at once; the album page and the cover are fetched in the background, one at a time
    def link(self, row: batch.Row, url: str, candidate=None, keep_both: bool = True, notify: bool = True, advance: bool = True) -> None:
        """The file is linked to the album in the library now. What the site has to say about it (page, series, cover) is queued;
        unlinking the file meanwhile cancels it, and quitting leaves it to `bdnex import --fetch`."""
        row.link_id += 1
        started = time.monotonic()
        try:
            batch.link_now(self.library, row, candidate)
        except Exception as err:  # a file that moved, a locked library: say so on the row, the screen goes on
            self.link_failed(row, err)
            return
        took = time.monotonic() - started
        row.pending, row.queued_at, row.started_at = True, time.monotonic(), 0.0
        self.refresh_dest(row)  # the file is linked now (written at once): where a move would put it is known, the page is not needed
        self.marked.discard(id(row))
        self.decided["linked"] = self.decided.get("linked", 0) + 1
        logger.info("%s: %s (written in %.2f s); page and cover queued", os.path.basename(row.path), row.message, took)
        self.queue_details(row, url, row.link_id)
        row.message += " · page and cover are being fetched"
        self.refresh_row(row)
        if notify:
            self.notify(row.message, timeout=5)
        if advance:
            self.advance()

    def link_failed(self, row: batch.Row, err: Exception) -> None:
        logger.warning("%s: could not link: %s", os.path.basename(row.path), err, exc_info=True)
        row.busy, row.busy_what, row.pending = False, "", False
        row.message = f"not linked: {err}"
        self.refresh_row(row)
        self.notify(row.message, severity="error", timeout=10)

    def link_page(self, row: batch.Row, info) -> None:
        """The file is linked to an album read from a page the person gave (bdtheque.com): nothing else is fetched."""
        self.supersede(row)
        try:
            batch.link_info(self.library, row, info)
        except Exception as err:
            self.link_failed(row, err)
            return
        self.refresh_dest(row)
        self.marked.discard(id(row))
        self.decided["linked"] = self.decided.get("linked", 0) + 1
        logger.info("%s: %s", os.path.basename(row.path), row.message)
        self.refresh_row(row)
        self.notify(row.message, timeout=5)
        self.advance()

    def queue_details(self, row: batch.Row, url: str, mine: int) -> None:
        """Queue the fetching of a linked file's page and cover. With several endpoints the downloads of several files run at once
        (as many as `fetch.parallel`, or one per endpoint, like `bdnex import`); the library is written by one thread, a file at a time."""
        from bdnex.importer.registry import create_source
        from bdnex.ui.review_fetch import DetailsQueue, Job

        if self.fetcher is None:
            self.fetcher = DetailsQueue(self.settings, lambda library: create_source(self.settings.source, library, self.settings))
        self.unfetched += 1
        with self.remaining_lock:
            self.remaining += 1
        self.fetcher.submit(Job(url=url, path=row.path, wanted=lambda: row.link_id == mine,  # not unlinked or decided again meanwhile
                                started=lambda: self.details_started(row),
                                finished=lambda error: self.details_finished(row, mine, error),
                                run_import=lambda: self.import_details(row, url)))

    def details_started(self, row: batch.Row) -> None:
        """Its downloads begin: the state column says "fetching" and counts (several rows can be fetching at once)."""
        row.started_at = time.monotonic()
        logger.info("%s: fetching the page and cover now", os.path.basename(row.path))

    def import_details(self, row: batch.Row, url: str) -> bool:
        """In the queue's thread: the import of the album's page and cover for a file already linked (it finds what was downloaded)."""
        from bdnex.ui import verdicts

        captured = LineSink(self)
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            return verdicts.rematch(row.path, url, True, max(1, self.verbose))

    def details_finished(self, row: batch.Row, mine: int, error: str) -> None:
        """From the queue's thread: it is over, with an error in words or not (the others go on)."""
        with self.remaining_lock:
            self.remaining -= 1
        with contextlib.suppress(Exception):  # never `call_from_thread`: this thread may hold the library's write lock the screen waits for
            self.ui_loop.call_soon_threadsafe(self.details_done, row, mine, error)

    def details_done(self, row: batch.Row, mine: int, error: str) -> None:
        self.unfetched = max(0, self.unfetched - 1)
        if row.link_id != mine:
            # decided again meanwhile: if the import linked it back after an unlink, the unlink wins
            if row.status == "unlinked" and (self.library.get_file(row.path) or {}).get("album_id") is not None:
                batch.decide(self.library, row, "unlink")
            return
        row.pending = False
        self.refresh_dest(row)  # the import may have chosen another album than the one first linked (a spare, a switch): its place is the one that counts
        if row.started_at:
            logger.info("%s: page and cover done in %.1f s", os.path.basename(row.path), time.monotonic() - row.started_at)
        if error:
            row.message = f"linked; page and cover not fetched ({error}): `bdnex import --fetch` tries again"
            self.notify(f"{os.path.basename(row.path)}: {row.message}", severity="warning", timeout=8)
        else:
            file = self.library.get_file(row.path) or {}
            row.cover = file.get("cover_score")
            batch.refresh_link(self.library, row, browse.strong_of(self.settings))
            row.message = f"linked to {row.album} · page and cover fetched" + (
                f" · its cover is {row.cover:.0f}% like the file's" if row.cover is not None else "")
            logger.info("%s: %s", os.path.basename(row.path), row.message)
        if row in self.rows:
            self.refresh_row(row)

    def action_next_batch(self) -> None:
        if self.loading or self.page_step(1):
            return
        if self.at < len(self.batches) - 1:  # came back with `b`: the batch after this one is already known
            self.go_to(self.at + 1)
        else:
            self.load_batch()

    def action_previous_batch(self) -> None:
        if self.loading or self.page_step(-1):
            return
        if self.at <= 0:
            self.notify("This is the first batch.", timeout=3)
            return
        self.go_to(self.at - 1)

    def go_to(self, index: int) -> None:
        """Show a batch seen before, with the decisions made in it (and the fetches still running for its rows)."""
        if self.rows:
            self.batches[self.at] = self.rows  # a sort replaced the list (never an emptied one: see `load_batch`)
        self.at = index
        self.number = index + 1
        self.display(self.batches[index], "seen before")


def run(library, settings, source, size: int = 50, terms=(), unmatched: bool = False, everything: bool = False,
        again: bool = False, verbose: int = 0, tree: Optional[bool] = None, resume: Optional[dict] = None) -> dict[str, int]:
    """Run the table until the person quits; returns how many files were decided, by decision. ``resume``: the saved place (``--continue``)."""
    app = ReviewApp(library, settings, source, size, terms, unmatched, everything, again, verbose, tree, resume)
    app.finish_fetches = True
    app.run()
    run.unfetched = app.finish_pending_fetches()  # the caller says what is left for `bdnex import --fetch`
    return app.decided
