"""The columns of the review table: a registry (any field of a file or its album can be a column), the view that is shown, and where it is kept.

The view (which columns, in which order, the size of the panes, the left panel's tab) is saved in the library's ``meta`` table under
``review.view`` each time it changes, so the screen opens as it was left. Double-click a header to change that column's field; ``C`` picks them all.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from typing import Optional

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, OptionList, SelectionList
from textual.widgets.option_list import Option

logger = logging.getLogger(__name__)

# key: (title, width). The order here is the order in the table; DEFAULT_VIEW is what a screen that was never changed shows.
COLUMN_TYPES = {
    "num": ("#", 3), "file": ("File", 40), "folder": ("Folder", 22), "album": ("Album now", 40), "page": ("Page", 8), "match": ("Match", 8),
    "cover": ("Cover", 8), "link": ("Link", 13), "review": ("Review", 19), "dest": ("Would go to", 44), "kind": ("Kind", 7), "vol": ("Vol", 5),
    "series": ("Series", 24), "title": ("Title", 28), "category": ("Category", 9), "collection": ("Collection", 16),
    "publisher": ("Publisher", 14), "year": ("Year", 5), "authors": ("Authors", 22), "genres": ("Genres", 18), "language": ("Lang", 5),
    "format": ("Format", 6), "size": ("Size", 8), "pages": ("Pages", 6), "reviewed": ("Reviewed", 9), "spare": ("Spare", 5),
    "relation": ("Relation", 9),
}
DEFAULT_VIEW = ("num", "file", "folder", "album", "page", "match", "cover", "link", "review", "dest", "kind", "vol", "series")
COLUMNS = tuple(COLUMN_TYPES[key] for key in DEFAULT_VIEW)  # (title, width) of the default view: what the tests and the number keys know
FLEX_COLUMN = DEFAULT_VIEW.index("dest")  # `Would go to` takes the room the others leave
VIEW_KEY = "review.view"


class ColumnsScreen(ModalScreen):
    """Which columns the table shows: tick them (space), Enter or the button saves. They keep the order of this list."""

    BINDINGS = [Binding("escape", "close", "Cancel")]
    CSS = """
    ColumnsScreen { align: center middle; }
    #columns { width: 50; height: 90%; border: round $accent; padding: 1 2; background: $surface; }
    #columnlist { height: 1fr; }
    #columnbuttons { height: 3; align-horizontal: right; }
    #columnbuttons Button { margin-left: 1; }
    """

    def __init__(self, shown: list[str]) -> None:
        super().__init__()
        self.shown = shown

    def compose(self) -> ComposeResult:
        with Vertical(id="columns"):
            yield Label(Text("Columns of the table", style="bold"))
            yield Label(Text("space ticks or unticks one; double-click a header in the table to change that column's field", style="dim"))
            yield SelectionList(*[(f"{title}", key, key in self.shown) for key, (title, _) in COLUMN_TYPES.items()], id="columnlist")
            with Horizontal(id="columnbuttons"):
                yield Button("Default", id="default")
                yield Button("Save", id="ok", variant="success")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        box = self.query_one(SelectionList)
        if event.button.id == "default":
            box.deselect_all()
            for key in DEFAULT_VIEW:
                box.select(key)
        elif event.button.id == "ok":
            chosen = [key for key in COLUMN_TYPES if key in set(box.selected)]
            self.dismiss(chosen or list(DEFAULT_VIEW))
        else:
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class ColumnChoiceScreen(ModalScreen):
    """The field of one column: pick another (the one it replaces is hidden), or hide it."""

    BINDINGS = [Binding("escape", "close", "Cancel")]
    CSS = """
    ColumnChoiceScreen { align: center middle; }
    #choice { width: 44; height: 80%; border: round $accent; padding: 1 2; background: $surface; }
    """

    def __init__(self, current: str, shown: list[str]) -> None:
        super().__init__()
        self.current, self.shown = current, shown

    def compose(self) -> ComposeResult:
        with Vertical(id="choice"):
            yield Label(Text(f"Show instead of {COLUMN_TYPES[self.current][0]!r}", style="bold"))
            options = [Option("(hide this column)", id="")]
            options += [Option(title, id=key) for key, (title, _) in COLUMN_TYPES.items() if key not in self.shown or key == self.current]
            yield OptionList(*options, id="choices")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id if event.option.id is not None else "")

    def action_close(self) -> None:
        self.dismiss(None)


class ReviewTable(DataTable):
    """The table of the review screen: it tells the app about clicks (a Textual app does not receive the clicks of its widgets)."""

    def on_click(self, event: events.Click) -> None:
        self.app.table_clicked(event)


class ColumnsMixin:
    """The table's columns, their cells, their sort keys, and the saved view."""

    view: list[str]
    view_extra: dict

    # -- the saved view
    def load_view(self) -> None:
        self.view, self.view_extra = list(DEFAULT_VIEW), {}
        with contextlib.suppress(Exception):
            saved = json.loads(self.library.get_meta(VIEW_KEY) or "{}")
            wanted = [item for key in saved.get("columns", []) for item in (("link", "review") if key == "state" else (key,))]  # (the old State column)
            keys = [key for key in wanted if key in COLUMN_TYPES]
            if keys:
                self.view = keys
            self.view_extra = {k: v for k, v in saved.items() if k != "columns"}

    def save_view(self) -> None:
        with contextlib.suppress(Exception):
            if self.library.path != ":memory:":
                self.library.set_meta(VIEW_KEY, json.dumps({"columns": self.view, **self.view_extra}))

    def remember(self, **values) -> None:
        self.view_extra.update(values)
        self.save_view()

    # -- the columns
    def add_columns(self) -> None:
        """The columns, with an arrow on the sorted one. Every column has a width of its own: a Textual ``DataTable`` measures
        auto-width columns late (Textualize/textual#6759), so a table that is rebuilt (here: by a sort) could paint its first rows
        with stale widths. Long names are cut, so the scores and the state are always on screen."""
        table = self.query_one("DataTable")
        fixed = sum(COLUMN_TYPES[key][1] for key in self.view if key != "dest")
        for index, key in enumerate(self.view):
            title, width = COLUMN_TYPES[key]
            arrow = (" ▼" if self.sort_reverse else " ▲") if index == self.sort_column else ""
            if key == "dest":  # a path is long: it takes what the screen has left (at least `width`)
                width = max(width, self.table_width() - fixed - 2 * len(self.view) - 2)
            table.add_column(f"{title}{arrow}", width=width, key=str(index))  # (a fixed width: the table never has to measure its cells)

    def cells(self, number: int, row) -> list:
        return [self.cell(key, number, row) for key in self.view]

    def album_dict(self, row) -> dict:
        if row.album_id is None:
            return {}
        found = self.album_data.get(row.album_id)
        if found is None:
            found = self.album_data[row.album_id] = self.library.get_album(row.album_id) or {}
        return found

    def file_dict(self, row) -> dict:
        found = self.file_data.get(row.path)
        if found is None:
            found = self.file_data[row.path] = self.library.get_file(row.path) or {}
        return found

    def forget_cached(self) -> None:
        self.album_data.clear()
        self.file_data.clear()

    def album_facts(self, row) -> tuple[str, Optional[int]]:
        """``(series | oneshot | "", volume)`` of the row's album: from the row when a selection built it, else read once from the library."""
        if row.album_kind:
            return row.album_kind, row.volume
        album = self.album_dict(row)
        return album.get("kind") or "", album.get("volume")

    def cell(self, key: str, number: int, row) -> Text:
        from bdnex.formatting import human_size
        from bdnex.ui import review_tui as t

        if key == "num":
            return Text(str(number), style="bold reverse magenta" if id(row) in self.marked else "cyan")
        if key == "file":
            return Text(os.path.basename(row.path), style="bold")
        if key == "folder":
            return Text(row.folder, style="dim")
        if key == "album":
            return t.album_text(row)
        if key == "page":
            return t.page_text(row.album_url)
        if key == "cover":
            return t.score_text(row.cover)
        if key == "match":
            return Text("you", style="cyan") if row.status == "linked" else t.score_text(row.score)
        if key == "link":
            return self.link_cell(row)
        if key == "review":
            return self.review_cell(row)
        if key == "dest":
            return Text(row.dest or ("…" if self.planned is None else "-"),
                        style="dim" if row.dest != "?" and not row.dest.startswith("(") else "yellow")
        if key == "kind":
            kind, _ = self.album_facts(row)
            return {"series": Text("series", style="cyan"), "oneshot": Text("1-shot", style="magenta")}.get(kind, Text("-", style="dim"))
        if key == "vol":
            _, volume = self.album_facts(row)
            special = self.album_dict(row).get("alternate_number") if volume is None else None  # a tome with no number: HS 1, INT...
            word = str(volume) if volume is not None else (special or "-")
            return Text(word, style="dim" if word == "-" else "")
        album, file = self.album_dict(row), self.file_dict(row)
        text = ""
        if key == "series":
            text = album.get("series") or ""
        elif key == "title":
            text = album.get("title") or ""
        elif key in ("category", "collection", "publisher", "language"):
            text = album.get(key) or ""
        elif key == "year":
            text = str(album["year"]) if album.get("year") else ""
        elif key == "authors":
            credits = album.get("credits") or {}
            text = ", ".join(dict.fromkeys([*credits.get("Writer", []), *credits.get("Penciller", [])]))
        elif key == "genres":
            text = ", ".join(album.get("genres") or [])
        elif key == "format":
            text = file.get("format") or ""
        elif key == "size":
            text = human_size(file["size"]) if file.get("size") else ""
        elif key == "pages":
            text = str(file["page_count"]) if file.get("page_count") else ""
        elif key == "reviewed":
            text = row.status or row.previous or ""
        elif key == "spare":
            text = "spare" if (row.spare or file.get("spare")) else ""
        elif key == "relation":
            text = (f"{album['relation']} of {album['parent_id']}" if album.get("parent_id") is not None else "") if album else ""
        return Text(text or "-", style="" if text else "dim")

    LINK_STYLE = {"auto matched": "cyan", "manual": "cyan", "medium match": "dark_orange", "doubtful": "red", "no album": "red"}

    def link_of(self, row) -> str:
        """How the file is linked, in the words of the Link column (the rows of a selection carry it; the doubtful batch's are worked out from their verdict)."""
        if row.link:
            return "no album" if row.album_id is None else row.link
        return {"unmatched": "no album", "wrong": "doubtful", "unconfirmed": "medium match"}.get(row.kind, "auto matched")

    def link_cell(self, row) -> Text:
        word = self.link_of(row)
        return Text(word, style=self.LINK_STYLE.get(word, ""))

    @staticmethod
    def confirmed_how(row) -> str:
        """``kept`` / ``linked`` / ``no album online`` when you confirmed the file (this session, or an earlier run), else ``""``."""
        said = row.status if row.status in ("keep", "linked", "left") else {"keep": "keep", "other": "linked", "leave": "left"}.get(
            (row.previous or "").split(":")[0], "") if not row.status else ""
        return {"keep": "kept", "linked": "linked", "left": "no album online"}.get(said, "")

    def review_cell(self, row) -> Text:
        import time

        from bdnex.ui import review_tui as t

        spin = t.SPINNER[int(time.monotonic() * 10) % len(t.SPINNER)]  # moves with every redraw: something is going on
        if row.busy:
            waited = f" {time.monotonic() - row.started_at:.0f}s" if row.busy_what and row.started_at else ""
            return Text(f"{spin} {row.busy_what or 'linking'}{waited}", style="bold yellow")
        if row.pending:
            now = time.monotonic()
            return Text(f"{spin} linked · fetching {now - row.started_at:.0f}s" if row.started_at else
                        f"linked · queued {now - row.queued_at:.0f}s", style="yellow")
        if row.message.startswith(("refused", "failed")):
            return Text(row.message.split(":")[0], style="bold red")
        how = self.confirmed_how(row)
        if how:
            return Text(f"confirmed · {how}", style="green")
        if row.status in ("unlinked", "skipped") or (row.previous or "").startswith("unlink"):
            return Text("skipped" if row.status == "skipped" else "unlinked", style="yellow")
        return Text("-", style="dim")

    def sort_key(self, index: int, row):
        """What the column ``index`` sorts by; ``None`` puts the row after the others, whatever the direction."""
        from bdnex.ui import review_tui as t

        key = self.view[index] if 0 <= index < len(self.view) else ""
        if key == "num":
            return self.order.get(id(row), 0)
        if key == "file":
            return os.path.basename(row.path).casefold()
        if key == "folder":
            return row.folder.casefold()
        if key == "album":
            return row.album.casefold() if row.album else None
        if key == "page":
            return int(t.page_number(row.album_url)) if t.page_number(row.album_url) else None
        if key == "match":
            return row.score
        if key == "cover":
            return row.cover
        if key == "dest":
            return row.dest.casefold() if row.dest else None
        if key == "kind":
            return self.album_facts(row)[0] or None
        if key == "vol":
            return self.album_facts(row)[1]
        if key == "link":
            return {"doubtful": 0, "no album": 1, "medium match": 2, "auto matched": 3, "manual": 4}.get(self.link_of(row), 5)
        if key == "review":
            return 1 if self.confirmed_how(row) else 0
        album, file = self.album_dict(row), self.file_dict(row)
        value = {"series": album.get("series"), "title": album.get("title"), "category": album.get("category"),
                 "collection": album.get("collection"), "publisher": album.get("publisher"), "year": album.get("year"),
                 "language": album.get("language"), "format": file.get("format"), "size": file.get("size"), "pages": file.get("page_count"),
                 "reviewed": row.status or row.previous or None, "spare": 1 if (row.spare or file.get("spare")) else None,
                 "relation": album.get("relation"), "authors": ", ".join((album.get("credits") or {}).get("Writer", [])) or None,
                 "genres": ", ".join(album.get("genres") or []) or None}.get(key)
        return value.casefold() if isinstance(value, str) else value

    # -- choosing columns
    def action_columns(self) -> None:
        self.push_screen(ColumnsScreen(self.view), self.set_view)

    def set_view(self, keys) -> None:
        if not keys:
            return
        self.view = list(keys)
        self.sort_column = None
        self.save_view()
        self.render_table()

    def table_clicked(self, event: events.Click) -> None:
        """Double-click a header: change that column's field. Ctrl-click a row: mark it, like space."""
        with contextlib.suppress(Exception):
            meta = event.style.meta
            if "column" not in meta:
                return
            if event.shift and meta.get("row", -1) >= 0 and self.mark_anchor is not None:
                self.mark_range(self.mark_anchor, meta["row"])  # a list: from the last marked row to this one
            elif event.ctrl and meta.get("row", -1) >= 0:
                self.toggle_mark_at(meta["row"])
            elif meta.get("row") == -1 and event.chain >= 2:
                index = meta["column"]
                self.double_header = True  # the two clicks sorted the column: the sort that follows this one is skipped
                if 0 <= index < len(self.view):
                    self.push_screen(ColumnChoiceScreen(self.view[index], self.view), lambda key: self.swap_column(index, key))

    mark_anchor: Optional[int] = None  # the row last marked: a shift-click marks from it to the one clicked

    def toggle_mark_at(self, index: int) -> None:
        """Mark or unmark the row at ``index`` (a ctrl-click does what space does, without moving the cursor)."""
        if 0 <= index < len(self.rows) and not self.rows[index].busy:
            row = self.rows[index]
            self.marked.symmetric_difference_update({id(row)})
            self.mark_anchor = index
            self.refresh_row(row)
            self.notify(f"{len(self.marked)} file(s) marked", timeout=2)

    def mark_range(self, first: int, last: int) -> None:
        """Mark every row from ``first`` to ``last`` (shift-click, or shift with the arrows)."""
        for index in range(max(0, min(first, last)), min(len(self.rows) - 1, max(first, last)) + 1):
            row = self.rows[index]
            if not row.busy and id(row) not in self.marked:
                self.marked.add(id(row))
                self.refresh_row(row)
        self.mark_anchor = last
        self.notify(f"{len(self.marked)} file(s) marked", timeout=2)

    def action_extend_mark(self, step: int) -> None:
        """Shift with the arrows: mark this row and the next one in that direction, and move on (a list is marked by walking down it)."""
        table = self.query_one("DataTable")
        here = table.cursor_row
        if not (0 <= here < len(self.rows)):
            return
        there = max(0, min(len(self.rows) - 1, here + step))
        self.mark_range(here, there)
        table.move_cursor(row=there)

    def swap_column(self, index: int, key) -> None:
        if key is None or not (0 <= index < len(self.view)):
            return
        if key == "":
            if len(self.view) > 1:
                del self.view[index]
        else:
            self.view[index] = key
        self.sort_column = None
        self.save_view()
        self.render_table()
