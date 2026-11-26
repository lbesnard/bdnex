"""The Write and Move buttons of the review screen: ``bdnex write`` and ``bdnex move`` for the files of the view on the screen.

The view is the selection that is shown (a folder, filters, ticks: every page of it, not only the page on screen), or the batch. A first
run in the background says what would change (nothing is touched, the library is opened read-only), then ``Do it`` does it, with its own
library connection, while a window shows the progress and keeps the rest of the screen still. Nothing is asked per file: it is the
``--force`` of the commands (the database wins, a name that is taken gets a number, originals are kept), and a file under a read-only
``copy`` source is refused as the commands refuse it.
"""

from __future__ import annotations

import contextlib
import io
import os
from collections import Counter

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Label, Static
from textual.worker import get_current_worker

from bdnex.ui.review_modal import Modal

LIMIT = 1000  # files read from the library at a time
SAMPLES = 8  # files shown as examples in the preview

TEXT = {
    "write": ("Write the tags", "writes ComicInfo.xml into the files, where they are (a PDF or CBR is converted to a CBZ beside it if `write.pdf` / `write.cbr` say so)"),
    "move": ("Move the files", "writes the tags AND renames and moves the files into the tree your `paths` templates give (the `Organised` tab shows it)"),
}


class OrganiseScreen(Modal):
    """What would change, a button to do it, then the progress and the result. Dismisses with ``True`` when files were changed, else ``None``."""

    BINDINGS = [Binding("escape", "close", "Close")]
    CSS = """
    OrganiseScreen { align: center middle; }
    #organise { width: 90%; height: 80%; border: round $accent; padding: 1 2; background: $surface; }
    #organisebody { height: 1fr; }
    #organisebuttons { height: 3; align-horizontal: right; }
    #organisebuttons Button { margin-left: 2; }
    """

    def __init__(self, mode: str, view: str) -> None:
        super().__init__()
        self.mode, self.view = mode, view
        self.stage = "preview"  # preview -> ready -> running -> done
        self.changed = False

    def compose(self) -> ComposeResult:
        title, what = TEXT[self.mode]
        with Vertical(id="organise"):
            yield Label(Text(f"{title}: {self.view}", style="bold"))
            yield Label(Text(what, style="dim"))
            with VerticalScroll(id="organisebody"):
                yield Static("Working out what would change (nothing is touched)...", id="organisetext")
            with Vertical(id="organisebuttons"):
                yield Button("Do it", id="go", variant="success", disabled=True,
                             tooltip="Do it for every file listed. Nothing is asked per file: a name already taken gets a number and originals are kept.")
                yield Button("Cancel", id="cancel", tooltip="Close without changing anything (Esc).")

    def say(self, text) -> None:
        self.query_one("#organisetext", Static).update(text)

    def ready(self, text: Text, can_run: bool) -> None:
        """The preview is in: what would change, and the button when there is something to do."""
        self.stage = "ready"
        self.say(text)
        self.query_one("#go", Button).disabled = not can_run
        if can_run:
            self.query_one("#go", Button).focus()

    def progress(self, text: str) -> None:
        self.say(text)

    def finished(self, text: Text, changed: bool) -> None:
        self.stage, self.changed = "done", changed
        self.say(text)
        self.query_one("#go", Button).display = False
        cancel = self.query_one("#cancel", Button)
        cancel.label, cancel.disabled = "Close", False
        cancel.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "go" and self.stage == "ready":
            self.stage = "running"
            event.button.disabled = True
            self.query_one("#cancel", Button).disabled = True  # (a run is not stopped half way: the files done are done)
            self.say("Starting...")
            self.app.organise_run(self.mode)
        elif event.button.id == "cancel":
            self.action_close()

    def action_close(self) -> None:
        if self.stage == "running":
            return
        self.dismiss(True if self.changed else None)


class ReviewOrganise:
    """The mixin of the review app: ``W`` and ``M`` (and the two buttons under the table) open the window."""

    def action_write_view(self) -> None:
        self.organise_open("write")

    def action_move_view(self) -> None:
        self.organise_open("move")

    def view_name(self) -> str:
        selection = getattr(self.rows, "selection", None)
        if selection:
            total = getattr(self.rows, "total", len(self.rows))
            return f"{selection[1]} ({total} file{'s' if total != 1 else ''})"
        return f"the batch ({len(self.rows)} file{'s' if len(self.rows) != 1 else ''})"

    def organise_open(self, mode: str) -> None:
        if not self.rows or self.loading:
            self.notify("Nothing on the screen to " + ("write" if mode == "write" else "move") + ".", timeout=3)
            return
        if getattr(self, "organising", False):
            return
        self.organising = True
        screen = OrganiseScreen(mode, self.view_name())
        self.push_screen(screen, lambda changed: self.organise_closed(changed))
        selection = getattr(self.rows, "selection", None)
        self.run_worker(lambda: self.organise_work(mode, True, selection[0] if selection else None, [r.file_id for r in self.rows], screen),
                        thread=True, group="organise", exclusive=True)

    def organise_run(self, mode: str) -> None:
        screen = self.screen
        selection = getattr(self.rows, "selection", None)
        self.run_worker(lambda: self.organise_work(mode, False, selection[0] if selection else None, [r.file_id for r in self.rows], screen),
                        thread=True, group="organise", exclusive=True)

    def organise_closed(self, changed) -> None:
        self.organising = False
        if changed:
            self.organise_refresh()

    def organise_work(self, mode: str, preview: bool, picks, file_ids: list, screen: OrganiseScreen) -> None:
        """In a worker: the files of the view, then ``Organizer.run`` over them (a preview is read-only and writes nothing)."""
        from bdnex.library import Library
        from bdnex.organizer.organizer import MOVE, WRITE, Organizer

        worker = get_current_worker()
        try:
            with Library.for_settings(self.settings, readonly=preview) as library:
                ids = self.organise_ids(library, picks, file_ids)
                rows = [row for row in (library.file_by_id(i) for i in ids) if row and row.get("album_id") is not None]
                skipped = len(ids) - len(rows)
                organizer = Organizer(library, self.settings, prompter=None, pretend=preview, force=True)
                results = organizer.run(
                    rows, WRITE if mode == "write" else MOVE,
                    progress=None if preview else (lambda text: self.call_from_thread(screen.progress, f"{text}\n\nThe window closes by itself when it is done: wait for it.")),
                    on_result=None)
            hooked = 0
            if not preview:
                from bdnex.ui.hooks import run_hooks

                done = sum(1 for r in results if r.outcome in ("written", "moved", "copied"))
                with contextlib.suppress(Exception):
                    hooked = run_hooks(self.settings, "after_write" if mode == "write" else "after_move", done, mode, stream=io.StringIO())
        except Exception as err:
            if not worker.is_cancelled:
                self.call_from_thread(self.organise_failed, screen, str(err))
            return
        if worker.is_cancelled:
            return
        self.call_from_thread(self.organise_report, screen, mode, preview, results, skipped, hooked if not preview else 0)

    def organise_ids(self, library, picks, file_ids: list) -> list[int]:
        """The file ids of the view: every page of the selection, or the rows of the batch (in the order the table has)."""
        from bdnex.library import browse

        if not picks:
            return [i for i in file_ids if i is not None]
        ids: list[int] = []
        offset = 0
        while True:
            page = browse.rows_for_many(library, self.settings, picks, self.planned, LIMIT, offset)
            ids += [row.file_id for row in page if row.file_id is not None]
            offset += LIMIT
            if offset >= getattr(page, "total", 0) or not len(page):
                return ids

    def organise_failed(self, screen: OrganiseScreen, message: str) -> None:
        screen.finished(Text(f"It could not be done: {message}", style="red"), False)

    def organise_report(self, screen: OrganiseScreen, mode: str, preview: bool, results: list, skipped: int, hooks: int) -> None:
        root = str(self.settings.collection_path or "")
        counts = Counter(r.outcome for r in results)
        text = Text()
        if preview:
            moves = [r for r in results if r.dest and r.dest != r.path and r.outcome == "pretend"]
            todo = [r for r in results if r.outcome == "pretend" and (mode == "write" or r in moves or "write" in (r.note or ""))]
            text.append(f"{len(results)} file(s) looked at: ", style="bold")
            text.append(f"{len(todo)} would be changed" + (f", {len(moves)} of them moved" if mode == "move" else "") + ".\n", style="bold")
            for r in todo[:SAMPLES]:
                text.append(f"\n{os.path.basename(r.path)}\n", style="bold")
                if r.dest and r.dest != r.path:
                    text.append(f"   from  {os.path.relpath(r.path, root) if root and r.path.startswith(root) else r.path}\n", style="red")
                    text.append(f"   to    {os.path.relpath(r.dest, root) if root and r.dest.startswith(root) else r.dest}\n", style="green")
            if len(todo) > SAMPLES:
                text.append(f"\n... and {len(todo) - SAMPLES} more\n", style="dim")
            others = {name: n for name, n in counts.items() if name != "pretend"}
            if others:
                text.append("\nNot done: " + ", ".join(f"{n} {name}" for name, n in others.items()) + " (for example a file with a read-only source or no album).\n", style="yellow")
            if skipped:
                text.append(f"{skipped} file(s) have no album: nothing to write for them.\n", style="yellow")
            text.append("\nNothing was changed yet. 'Do it' does the above (`bdnex undo` can take a move back).", style="dim")
            screen.ready(text, bool(todo))
            return
        done = sum(counts.get(name, 0) for name in ("written", "moved", "copied"))
        text.append(f"{done} file(s) done" + (": " if counts else "."), style="bold green")
        text.append(", ".join(f"{n} {name}" for name, n in counts.items()) + "\n")
        failed = [r for r in results if r.outcome == "failed"]
        for r in failed[:SAMPLES]:
            text.append(f"\n{os.path.basename(r.path)}: {r.note}\n", style="red")
        if skipped:
            text.append(f"\n{skipped} file(s) have no album: nothing was written for them.\n", style="yellow")
        if hooks:
            text.append(f"\n{hooks} of your hooks ran.\n", style="dim")
        screen.finished(text, done > 0)

    def organise_refresh(self) -> None:
        """Files moved or rewritten: the counts and the folder tree are read again, and the same selection is shown again (the paths changed)."""
        self.plan_dirty.update(r.album_id for r in self.rows if r.album_id is not None)
        selection = getattr(self.rows, "selection", None)
        if selection:
            self.open_selections(selection[0], selection[1], selection[2])
        else:
            for row in self.rows:
                file = self.library.file_by_id(row.file_id) if row.file_id is not None else None
                if file:
                    row.path = file["path"]
                    self.refresh_row(row)
        self.schedule_counts()
        self.notify("Done: the view is read again.", timeout=4)
