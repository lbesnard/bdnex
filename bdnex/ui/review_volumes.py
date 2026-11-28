"""The question "link these files to the volumes of this series?": a table of each file and the album it would get, that can be corrected.

The volume read from a file name is sometimes wrong (a title with a number, a special, a chapter). Instead of a plain yes or no, the person
sees every file next to its album, picks another album for a file (the same search as ``o``), leaves a file out, and then links.
"""

from __future__ import annotations

import os
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable, Label

from bdnex.ui.review_modal import Modal

NONE_TEXT = "(no album: left as it is)"


def album_name(candidate) -> str:
    return candidate.url.rsplit("/", 1)[-1].removesuffix(".html")


class VolumesScreen(Modal):
    """``pairs``: ``(row, candidate or None)`` for every file. Dismisses with the ``(row, candidate)`` to link, or ``None`` for Cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("y", "link", "Link", show=False),
                Binding("x", "leave", "Leave out", show=False), Binding("delete", "leave", "Leave out", show=False)]
    CSS = """
    VolumesScreen { align: center middle; }
    #volumes { width: 90%; height: 85%; border: round $accent; padding: 1 2; background: $surface; }
    #volumestable { height: 1fr; margin: 1 0; }
    #volumesbuttons { height: 3; align-horizontal: right; }
    #volumesbuttons Button { margin-left: 2; }
    """

    def __init__(self, heading: str, pairs: list, hub=None, series: str = "", describe=album_name, pick=None, note=None):
        """``describe``: the text of an album; ``pick(index, done)``: opens the way to choose another one for a file (the album search by
        default); ``note(count)``: a line under the table that follows the number of files to link (what it will cost)."""
        super().__init__()
        self.heading, self.hub, self.series = heading, hub, series
        self.describe, self.picker, self.note = describe, pick, note
        self.rows = [row for row, _ in pairs]
        self.albums: list = [candidate for _, candidate in pairs]

    def compose(self) -> ComposeResult:
        with Vertical(id="volumes"):
            yield Label(Text(self.heading, style="bold"))
            yield Label(Text("Enter: pick another album for the file · x: leave the file out · y: link · Esc: cancel", style="dim"))
            yield DataTable(id="volumestable", cursor_type="row", zebra_stripes=True)
            yield Label("", id="volumesnote")
            with Horizontal(id="volumesbuttons"):
                yield Button("Link", id="link", variant="success", tooltip="Link the files that have an album (key y).")
                yield Button("Pick album", id="pick", tooltip="Another album for the file under the cursor: the search of `o` (Enter).")
                yield Button("Leave out", id="leave", tooltip="This file keeps what it has (key x).")
                yield Button("Cancel", id="cancel", tooltip="Link nothing (Esc).")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("File", "Album")
        for row, candidate in zip(self.rows, self.albums):
            table.add_row(os.path.basename(row.path), self.cell(candidate))
        table.focus()
        self.count()

    def cell(self, candidate) -> Text:
        return Text(self.describe(candidate)) if candidate is not None else Text(NONE_TEXT, style="dim")

    def count(self) -> None:
        total = sum(c is not None for c in self.albums)
        button = self.query_one("#link", Button)
        button.label = f"Link {total} file(s)"
        button.disabled = not total
        if self.note:
            self.query_one("#volumesnote", Label).update(Text(self.note(total), style="dim"))

    def current(self) -> Optional[int]:
        table = self.query_one(DataTable)
        return table.cursor_row if self.rows and 0 <= table.cursor_row < len(self.rows) else None

    def set_album(self, index: int, candidate) -> None:
        self.albums[index] = candidate
        self.query_one(DataTable).update_cell_at(Coordinate(index, 1), self.cell(candidate))
        self.count()

    def action_leave(self) -> None:
        index = self.current()
        if index is not None:
            self.set_album(index, None)

    def action_pick(self) -> None:
        index = self.current()
        if index is None:
            return
        if self.picker is not None:
            self.picker(index, lambda candidate: self.set_album(index, candidate) if candidate is not None else None)
            return
        from bdnex.importer.folders import title_and_volume  # the volume read from the name seeds the search

        from bdnex.ui.review_tui import FuzzyScreen

        if self.hub is None:
            return
        row = self.rows[index]
        volume = title_and_volume(row.path)[1]
        start = f"{self.series} {volume}".strip() if volume is not None else self.series or os.path.basename(row.path)
        self.app.push_screen(FuzzyScreen(self.hub, start, "Which album is this file?", about=[os.path.basename(row.path)]),
                             lambda candidate: self.set_album(index, candidate) if candidate is not None else None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_pick()

    def action_link(self) -> None:
        pairs = [(row, candidate) for row, candidate in zip(self.rows, self.albums) if candidate is not None]
        if pairs:
            self.dismiss(pairs)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"link": self.action_link, "pick": self.action_pick, "leave": self.action_leave, "cancel": self.action_cancel}[event.button.id]()


class ChoiceScreen(Modal):
    """A short list to choose one thing from (the issues of a volume): Enter picks, Esc goes back. Dismisses with the value, or ``None``."""

    BINDINGS = [Binding("escape", "back", "Back")]
    CSS = """
    ChoiceScreen { align: center middle; }
    #choice { width: 70%; height: 80%; border: round $accent; padding: 1 2; background: $surface; }
    #choices { height: 1fr; margin-top: 1; }
    """

    def __init__(self, heading: str, options: list[tuple[str, object]]):
        super().__init__()
        self.heading, self.options = heading, options

    def compose(self) -> ComposeResult:
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        with Vertical(id="choice"):
            yield Label(Text(self.heading, style="bold"))
            yield OptionList(*[Option(text) for text, _ in self.options], id="choices")

    def on_mount(self) -> None:
        self.query_one("#choices").focus()

    def on_option_list_option_selected(self, event) -> None:
        event.stop()
        self.dismiss(self.options[event.option_index][1])

    def action_back(self) -> None:
        self.dismiss(None)
