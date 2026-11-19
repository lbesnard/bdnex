"""``bdnex review --batch``: a table to check the matches a batch of files at a time (Textual, the optional ``tui`` extra).

One row per file: its name, its folder, the album it is linked to, the match score and the cover score. The keys decide
and every decision is saved at once (``bdnex.library.review_batch``), so the session can stop at any moment and be
continued later by running the command again.
"""

from __future__ import annotations

import webbrowser
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
KIND_STYLE = {"wrong": "red", "unconfirmed": "yellow", "unmatched": "magenta"}
KIND_WORD = {"wrong": "contradicted", "unconfirmed": "unconfirmed", "unmatched": "no album"}
EVIDENCE_STYLE = {"-": "red", "+": "green", "?": "yellow", "f": "yellow"}


def score_text(value: Optional[float]) -> Text:
    """A score in the colour of its value (red low, green high); ``-`` when there is none."""
    if value is None:
        return Text("-", style="dim")
    return Text(f"{value:.0f}%", style=color.score_hex(float(value)))


def album_text(row: batch.Row) -> Text:
    return Text(row.album or "(no album)", style="bold" if row.album else KIND_STYLE["unmatched"])


class SearchScreen(ModalScreen):
    """Search the album list (the sitemaps) by words: type to filter, Enter picks, Ctrl-O opens its page, Esc goes back."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("ctrl+o", "open", "Open the page")]
    CSS = """
    SearchScreen { align: center middle; }
    #search { width: 90%; height: 80%; border: round $accent; padding: 1 2; background: $surface; }
    """

    def __init__(self, source, start: str, title: str):
        super().__init__()
        self.source, self.start, self.heading = source, start, title
        self.hits: list = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search"):
            yield Label(Text(self.heading, style="bold"))
            yield Input(value=self.start, placeholder="words of the album, e.g. 'alex ada'", id="words")
            yield OptionList(id="hits")
            yield Label(Text("Enter picks the album · Ctrl-O opens its page · Esc goes back", style="dim"))

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self.fill(self.start)

    def fill(self, text: str) -> None:
        options = self.query_one(OptionList)
        options.clear_options()
        self.hits = [pair for pair in self.source.candidates(text.strip(), limit=40)] if text.strip() else []
        from bdnex.ui.prompts import display_name

        options.add_options([Option(f"{score:>3.0f}  {display_name(candidate)}") for candidate, score in self.hits])
        if self.hits:
            options.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self.fill(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.pick(self.query_one(OptionList).highlighted)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.pick(event.option_index)

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


class ReviewApp(App):
    """The table of one batch, the detail of the row under the cursor, and the keys that decide."""

    TITLE = "bdnex review"
    CSS = """
    #table { height: 2fr; min-height: 8; }
    #detail { height: 1fr; min-height: 8; border-top: solid $accent; padding: 0 1; overflow-y: auto; }
    """
    BINDINGS = [
        Binding("k", "keep", "Keep"),
        Binding("u", "unlink", "Unlink"),
        Binding("o", "other", "Other album (search)"),
        Binding("i", "ident", "Id / address"),
        Binding("s", "skip", "Skip"),
        Binding("l", "leave", "Leave"),
        Binding("p", "page", "Open page"),
        Binding("n", "next_batch", "Next batch"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, library, settings, source, size: int = 50, terms=(), unmatched: bool = False):
        super().__init__()
        self.library, self.settings, self.source = library, settings, source
        self.batch_size, self.terms, self.unmatched = size, list(terms), unmatched
        self.rows: list[batch.Row] = []
        self.number = 0
        self.decided: dict[str, int] = {}

    # -- layout
    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for name, width in (("#", 3), ("File", 40), ("Folder", 22), ("Album now", 46), ("Match", 6), ("Cover", 6), ("State", 13)):
            table.add_column(name, width=width)  # long names are cut, so the scores and the state are always on screen
        self.load_batch()

    def load_batch(self) -> None:
        self.number += 1
        self.rows = batch.next_batch(self.library, self.settings, self.batch_size, self.terms, self.unmatched)
        self.render_table()
        if not self.rows:
            self.sub_title = "nothing left to check"
            self.query_one("#detail", Static).update(Text("Nothing left to check: every doubtful file has been looked at. "
                                                         "`bdnex import --skipped --auto=all` decides the unlinked ones again.",
                                                         style="green"))

    def render_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for number, row in enumerate(self.rows, start=1):
            table.add_row(*self.cells(number, row), key=str(number - 1))
        done = sum(1 for row in self.rows if row.status)
        self.sub_title = f"batch {self.number} · {done}/{len(self.rows)} checked"
        if self.rows:
            table.move_cursor(row=next((i for i, row in enumerate(self.rows) if not row.status), 0))
            self.show_detail(table.cursor_row)

    def cells(self, number: int, row: batch.Row) -> list:
        import os

        # what was decided, or else what is wrong with it: the colour is for the thing to look at, not for every name
        state = Text(row.status, style=STATUS_STYLE.get(row.status, "")) if row.status else \
            Text(KIND_WORD.get(row.kind, row.kind), style=KIND_STYLE.get(row.kind, ""))
        return [Text(str(number), style="cyan"), Text(os.path.basename(row.path), style="bold"),
                Text(row.folder, style="dim"), album_text(row), score_text(row.score), score_text(row.cover), state]

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
            text.append(f"  {row.message}\n", style="yellow")
        self.query_one("#detail", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.show_detail(event.cursor_row)

    # -- the current row
    def current(self) -> Optional[batch.Row]:
        table = self.query_one(DataTable)
        return self.rows[table.cursor_row] if self.rows and 0 <= table.cursor_row < len(self.rows) else None

    def refresh_row(self, row: batch.Row) -> None:
        table = self.query_one(DataTable)
        index = self.rows.index(row)
        for column, cell in enumerate(self.cells(index + 1, row)):
            table.update_cell_at((index, column), cell)
        done = sum(1 for r in self.rows if r.status)
        self.sub_title = f"batch {self.number} · {done}/{len(self.rows)} checked"
        self.show_detail(index)

    def advance(self) -> None:
        """Move to the next row not decided yet; when none is left, offer the next batch."""
        table = self.query_one(DataTable)
        waiting = [i for i, row in enumerate(self.rows) if not row.status]
        if waiting:
            after = [i for i in waiting if i > table.cursor_row]
            table.move_cursor(row=(after or waiting)[0])
        elif self.rows:
            self.push_screen(AskScreen(f"Batch {self.number} is done. Check the next batch?", None), self.continue_with)

    def continue_with(self, answer) -> None:
        if answer:
            self.load_batch()
        elif answer is False:
            self.exit()

    # -- decisions: each one is saved at once
    def record(self, action: str) -> None:
        row = self.current()
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
        row = self.current()
        if row is not None:
            row.status = "skipped"  # nothing is saved: it is in the next batch again
            self.refresh_row(row)
            self.advance()

    def action_page(self) -> None:
        row = self.current()
        if row is not None and row.album_url:
            webbrowser.open(row.album_url)

    def action_other(self) -> None:
        row = self.current()
        if row is None:
            return
        from bdnex.importer.matching import query_from_filename

        start = query_from_filename(row.path)
        self.push_screen(SearchScreen(self.source, start, f"Which album is {row.path.rsplit('/', 1)[-1]}?"),
                         lambda candidate: self.link(row, candidate.url) if candidate is not None else None)

    def action_ident(self) -> None:
        row = self.current()
        if row is None:
            return

        def given(text) -> None:
            if not text:
                return
            candidate = self.source.candidate_for(text)
            if candidate is None:
                self.notify(f"{text!r} is not an album: give the address of its page, or its number.", severity="error")
                return
            self.link(row, candidate.url)

        self.push_screen(AskScreen("The album's page address, or its number:", ""), given)

    def link(self, row: batch.Row, url: str) -> None:
        """Link the file to an album: the import (it may read a page) runs in a thread, the library is written back here."""
        before = (self.library.get_file(row.path) or {}).get("album_id")
        row.message = "linking..."
        self.refresh_row(row)

        def work() -> None:
            from bdnex.ui import verdicts

            verdicts.rematch(row.path, url)
            self.call_from_thread(self.linked, row, before)

        self.run_worker(work, thread=True, exclusive=False)

    def linked(self, row: batch.Row, before) -> None:
        ok, message = batch.finish_link(self.library, row, before)
        self.notify(message, severity="information" if ok else "error")
        if ok:
            self.decided["linked"] = self.decided.get("linked", 0) + 1
        self.refresh_row(row)
        if ok:
            self.advance()

    def action_next_batch(self) -> None:
        self.load_batch()


def run(library, settings, source, size: int = 50, terms=(), unmatched: bool = False) -> dict[str, int]:
    """Run the table until the person quits; returns how many files were decided, by decision."""
    app = ReviewApp(library, settings, source, size, terms, unmatched)
    app.run()
    return app.decided
