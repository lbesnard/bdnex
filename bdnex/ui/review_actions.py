"""The Actions panel of the review screen: a list that slides in from the left, one click for what used to be a command line.

``A`` (or the ``☰ Actions`` chip at the top) opens it. Write, Move and Repair open their window (``review_organise``) for the view or the
whole collection. The other entries run the command of the command line (``update``, ``undo``, ``check``, ``trash``): the same ``run(argv, out)``
function in a worker thread, its output in a window, a preview first for the ones that have one. No second copy of any logic.
"""

from __future__ import annotations

import importlib
import io
import os
import threading
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.css.scalar import ScalarOffset
from textual.geometry import Offset
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Label, Static

from bdnex.ui.review_modal import Modal

# (group, ((id, label, tooltip), ...))
ENTRIES = (
    ("UNDO", (("undo", "Undo", "Takes back the last thing you did in this session: a Delete (the files come out of the trash) or a Move (the files go back where they were; a preview first). Key U."),)),
    ("WRITE the tags into the files", (("write:view", "This view", "`bdnex write` for the files of the view you are looking at (key W): the tags go into each file's ComicInfo.xml."),
                        ("write:all", "The whole collection", "`bdnex write` for every file of the library."))),
    ("SEND the tags to Komga", (("komga:view", "This view", "`bdnex komga push` for the files of the view: Komga's title, summary and credits. No file is changed. A preview first."),
                                ("komga:all", "The whole collection", "`bdnex komga push` for every file of the library. A preview first."))),
    ("MOVE the files", (("move:view", "This view", "`bdnex move` for the files of the view (key M)."),
                        ("move:all", "The whole collection", "`bdnex move` for every file of the library."))),
    ("REPAIR the damaged files", (("repair:view", "This view", "`bdnex repair` for the damaged files of the view (key H)."),
                                  ("repair:all", "The whole collection", "`bdnex repair` for every damaged file of the library."))),
    ("CHECK", (("quality", "Measure the quality (dpi)", "`bdnex check --quality`: the files that have no resolution yet. Then the Quality filter finds the low ones."),
               ("matches", "Weigh the matches", "`bdnex check --matches`: every match against its album's page; lists the ones that look wrong. Changes nothing."))),
    ("LIBRARY", (("update", "Read the changed files again", "`bdnex update`: files that another tool changed (size, checksum, pages, damage). A preview first."),
                 ("trash", "The trash", "`bdnex trash`: the files that were replaced. `bdnex trash --restore ID` puts one back."))),
    ("HELP", (("help", "Keys and the workflow", "The help screen (key ?)."),)),
)

# what each task runs: (title, what it does, argv of the preview or None, argv of the run, does it change files or the library)
TASKS = {
    "quality": ("Measure the quality (dpi)", "Reads about five pages of each file that has no resolution yet, several files at once, and stores the number. "
                "You can stop it and run it again: what is measured is kept.", None, ["--quality"], "check_cmd", True),
    "matches": ("Weigh the matches", "Weighs every match against its album's page and lists those that look wrong. Nothing is changed "
                "(`bdnex check --matches --fix` flags them for review).", None, ["--matches"], "check_cmd", False),
    "update": ("Read the changed files again", "Reads the files that changed on disk since they were recorded (another tool changed them). "
               "No file is touched.", ["-n"], [], "update_cmd", True),
    "komga": ("Send the tags to Komga", "Sends the library's title, summary, credits and the rest to the Komga server named in `komga.url`, for the files "
              "Komga also holds (paired by file name and size). Only what differs is sent. No comic file is changed; a file whose match waits for a review is left out.",
              None, [], "komga_cmd", False),
    "undo": ("Undo the last move", "Moves the files of the move run back to where they were (tags written into a file stay).", ["last", "-n"], ["last"], "undo_cmd", True),
    "trash": ("The trash", "The files that were replaced. `bdnex trash --restore ID` puts one back where it came from.", None, [], "trash_cmd", False),
}


class ActionsChip(Static):
    """``☰ Actions`` at the top: a click opens the panel (the ``A`` key does too)."""

    def __init__(self) -> None:
        super().__init__(Text(" ☰ Actions ", style="bold black on cyan"), id="actionschip")
        self.tooltip = "Write, move, repair, check and the other actions, in one list (key A)."

    def on_click(self) -> None:
        self.app.action_actions()


class ActionsScreen(Modal):
    """The panel: it slides in from the left; a click on an entry closes it and does it, Esc or a click outside closes it."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("A", "close", "Close", show=False)]
    CSS = """
    ActionsScreen { align: left top; background: $background 40%; }
    #actions { width: 40; height: 100%; border-right: vkey $accent; padding: 1 2; background: $surface; }
    #actions Label.group { margin-top: 1; text-style: bold; color: $accent; }
    #actions Button { width: 100%; margin-bottom: 0; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="actions"):
            yield Label(Text("Actions", style="bold"))
            for group, entries in ENTRIES:
                yield Label(group, classes="group")
                for entry, label, tip in entries:
                    yield Button(self.app.undo_label() if entry == "undo" else label, id="act-" + entry.replace(":", "-"), name=entry, tooltip=tip)

    def on_mount(self) -> None:
        panel = self.query_one("#actions")
        panel.styles.offset = (-40, 0)  # out of view to the left, then it slides in
        panel.styles.animate("offset", ScalarOffset.from_offset(Offset(0, 0)), duration=0.2)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.name)

    def on_click(self, event) -> None:
        if event.widget is self:  # a click on the dimmed rest of the screen
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class TaskScreen(Modal):
    """A command of the command line run in a window: a preview when it has one, then ``Do it``, its output as it comes, then ``Close``.

    Dismisses with ``True`` when it ran (the screen reads the library again), else ``None``.
    """

    BINDINGS = [Binding("escape", "close", "Close")]
    CSS = """
    TaskScreen { align: center middle; }
    #task { width: 90%; height: 80%; border: round $accent; padding: 1 2; background: $surface; }
    #taskbody { height: 1fr; }
    #taskstep { height: auto; margin-top: 1; text-style: bold; }
    #taskbuttons { height: 3; align-horizontal: right; }
    #taskbuttons Button { margin-left: 2; }
    """

    def __init__(self, key: str, batch: str = "last", terms: list[str] | None = None) -> None:
        super().__init__()
        self.key = key
        self.title_, self.what, self.preview_argv, self.run_argv, self.module, self.writes = TASKS[key]
        if key == "komga":  # `terms`: the query of the files to send (empty: the whole collection)
            self.preview_argv, self.run_argv = ["push", "-n", *(terms or [])], ["push", "-y", *(terms or [])]
        if key == "undo":  # the run that was made from this screen, or the last one of the library
            self.preview_argv, self.run_argv = [batch, "-n"], [batch]
        self.stage = "preview" if self.preview_argv else "ready"  # preview -> ready -> running -> done
        self.working: tuple[str, str, float] | None = None  # (what is going on, its colour, when it started): drawn with a spinner until it ends
        self.out = io.StringIO()
        self.lock = threading.Lock()
        self.ran = False

    def compose(self) -> ComposeResult:
        with Vertical(id="task"):
            yield Label(Text(self.title_, style="bold"))
            yield Label(Text(self.what, style="dim"))
            with VerticalScroll(id="taskbody"):
                yield Static("Working out what would change (nothing is touched)..." if self.preview_argv else "", id="tasktext")
            yield Label("", id="taskstep")  # which step this is: the preview (nothing sent), the work, or done
            with Horizontal(id="taskbuttons"):
                yield Button("Send metadata" if self.key == "komga" else "Do it", id="go", variant="success", disabled=bool(self.preview_argv) or not self.run_argv and self.key == "trash")
                yield Button("Cancel", id="cancel", tooltip="Close (Esc).")

    def on_mount(self) -> None:
        if self.key == "trash":  # a list: it runs at once, there is nothing to confirm
            self.query_one("#go", Button).display = False
            self.begin(self.run_argv, final=True)
        elif self.preview_argv:
            self.begin(self.preview_argv, final=False)
        else:
            self.query_one("#go", Button).focus()
        self.set_interval(0.2, self.show_output)

    def begin(self, argv: list[str], final: bool) -> None:
        with self.lock:
            self.out = io.StringIO()
        self.working = None
        if final:
            self.stage = "running"
            self.query_one("#cancel", Button).disabled = True
            self.ran = self.writes
            if self.preview_argv:
                self.query_one("#tasktext", Static).update("Working... (the list you saw was the preview; this is the real run)")
                self.working = ("Step 2 of 2: doing it now. Wait for it to finish.", "bold yellow", time.monotonic())
            else:
                self.working = ("Working. Wait for it to finish.", "bold yellow", time.monotonic())
        elif self.preview_argv:
            self.working = ("Step 1 of 2: reading and comparing, for a preview. Nothing is changed.", "bold cyan", time.monotonic())
        self.run_worker(lambda: self.work(argv, final), thread=True, group="task", exclusive=True)

    def work(self, argv: list[str], final: bool) -> None:
        """In a worker: the command of the command line, writing to a buffer the screen reads."""
        try:
            code = importlib.import_module(f"bdnex.ui.{self.module}").run(argv, out=self.out)
        except SystemExit as err:  # argparse
            code = err.code or 0
        except Exception as err:
            with self.lock:
                self.out.write(f"\nIt could not be done: {err}\n")
            code = 1
        self.app.call_from_thread(self.finished, final, code)

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def show_output(self) -> None:
        if self.working is not None:  # something is going on: a turning mark and the seconds, so that a wait is never silent
            what, style, since = self.working
            now = time.monotonic()
            self.query_one("#taskstep", Label).update(Text(f"{self.SPINNER[int(now * 10) % len(self.SPINNER)]}  {what}  ({int(now - since)} s)", style=style))
        with self.lock:
            text = self.out.getvalue()
        if text:
            lines = text.replace("\r", "\n").splitlines()
            self.query_one("#tasktext", Static).update(Text("\n".join(lines[-400:])))
            self.query_one("#taskbody", VerticalScroll).scroll_end(animate=False)

    def finished(self, final: bool, code) -> None:
        self.show_output()
        go, cancel = self.query_one("#go", Button), self.query_one("#cancel", Button)
        step = self.query_one("#taskstep", Label)
        self.working = None  # (the spinner stops)
        if final:
            self.stage = "done"
            step.update(Text("Finished. Close the window." if not code else "It stopped with an error (above). Close the window.", style="bold green" if not code else "bold red"))
            go.display = False
            cancel.label, cancel.disabled = "Close", False
            cancel.focus()
        else:
            self.stage = "ready"
            go.disabled = bool(code)
            step.update(Text(f"Step 1 of 2 done: this was only a preview, nothing was changed. Press {str(go.label)!r} to do it for real."
                             if not code else "The preview did not work: nothing was changed.", style="bold cyan" if not code else "bold red"))
            go.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "go" and self.stage == "ready":
            event.button.disabled = True
            self.begin(self.run_argv, final=True)
        elif event.button.id == "cancel":
            self.action_close()

    def action_close(self) -> None:
        if self.stage == "running":
            return
        self.dismiss(True if self.ran and self.stage == "done" else None)


class ReviewActions:
    """The mixin of the review app: ``A`` and the ``☰ Actions`` chip open the panel."""

    def action_actions(self) -> None:
        if isinstance(self.screen, (ActionsScreen, TaskScreen)) or getattr(self, "organising", False):
            return
        self.push_screen(ActionsScreen(), self.actions_chosen)

    def actions_chosen(self, chosen) -> None:
        if not chosen:
            return
        if chosen.startswith("komga:"):
            self.komga_open(chosen.split(":")[1])
        elif ":" in chosen:
            mode, scope = chosen.split(":")
            self.organise_open(mode, scope)
        elif chosen == "help":
            self.action_help()
        elif chosen == "undo":
            self.action_undo()
        else:
            self.push_screen(TaskScreen(chosen), self.task_closed)

    def komga_open(self, scope: str) -> None:
        """Send the tags to Komga: the whole collection (no query), or the files of the view (``fileid:`` and their numbers)."""
        if scope == "view":
            if not self.rows or self.loading:
                self.notify("Nothing on the screen to send.", timeout=3)
                return
            self.ask_pages("Send", lambda: self.komga_open_now(scope))
        else:
            self.komga_open_now(scope)

    def komga_open_now(self, scope: str) -> None:
        terms: list[str] = []
        if scope == "view":
            from bdnex.library import Library

            selection = getattr(self.rows, "selection", None)
            with Library.for_settings(self.settings, readonly=True) as library:
                ids = self.organise_ids(library, selection[0] if selection else None, [r.file_id for r in self.rows], "view")
            if not ids:
                self.notify("No file in this view to send.", timeout=3)
                return
            terms = ["fileid:" + ",".join(str(i) for i in ids)]
        self.push_screen(TaskScreen("komga", terms=terms), self.task_closed)

    # -- a record that looks unfetched because its file sits on the wrong one: found in the background, offered with `w`
    def look_for_repairs(self) -> None:
        """Once, after the screen is up: count the albums stored under another edition's address (``bdnex check --fix`` settles them). A cheap
        read in a worker; when there are some, the offer waits behind the red ``w`` mark like any other question."""
        def count() -> None:
            from bdnex.library import Library

            try:
                with Library.for_settings(self.settings, readonly=True) as library:
                    found = len(library.wrong_album_addresses())
            except Exception:  # a help: never a reason for the screen to fail
                return
            if found:
                self.call_from_thread(self.offer_repairs, found)

        self.run_worker(count, thread=True, group="repairs", exclusive=True)

    def offer_repairs(self, found: int) -> None:
        from bdnex.ui.review_tui import AskScreen

        text = (f"{found} album(s) are stored under the address of another edition, so a file linked that way can look unfetched (no summary, no year).\n"
                "Fix them now? Each album gets its own address back, and the files on the empty record move to it. "
                "Nothing else is changed (a copy of the library is not needed: it is the same as `bdnex check --fix`).")
        self.ask_later(f"fix {found} album address(es)?", lambda: AskScreen(text, None), lambda yes: self.repair_addresses() if yes else None)

    def repair_addresses(self) -> None:
        def work() -> None:
            from bdnex.library import Library

            try:
                with Library.for_settings(self.settings) as library:
                    restored, merged = library.repair_album_addresses()
            except Exception as err:
                self.call_from_thread(self.notify, f"Could not fix the addresses: {err}", severity="error", timeout=8)
                return
            self.call_from_thread(self.notify, f"Fixed: {restored} address(es) restored, {merged} empty record(s) merged.", timeout=6)
            self.call_from_thread(self.organise_refresh)

        self.run_worker(work, thread=True, group="repairs", exclusive=True)

    def task_closed(self, ran) -> None:
        if ran:
            self.organise_refresh()

    # -- Delete: any file goes to the trash (nothing is deleted for good), and the Actions panel (or U) puts the last ones back
    def action_delete_files(self) -> None:
        """The marked files, or the one under the cursor, to the trash after a yes / no. A file under a read-only ``copy`` source is refused."""
        from bdnex.ui.review_tui import AskScreen

        rows = [r for r in self.rows if id(r) in self.marked and not r.busy] or ([self.idle()] if self.idle() is not None else [])
        rows = [r for r in rows if r.status != "trashed"]
        if not rows:
            return
        refused = [r for r in rows if (self.settings.source_for(r.path) is not None and self.settings.source_for(r.path).mode == "copy")]
        rows = [r for r in rows if r not in refused]
        if refused:
            self.notify(f"{len(refused)} file(s) are in a read-only `copy` source: bdnex never deletes there.", severity="warning", timeout=6)
        if not rows:
            return
        names = "\n".join("  " + os.path.basename(r.path) for r in rows[:8]) + (f"\n  ... and {len(rows) - 8} more" if len(rows) > 8 else "")
        self.push_screen(AskScreen(f"Move {len(rows)} file(s) to the trash?\n{names}\n(not deleted for good: Undo in the Actions panel, or `bdnex trash --restore`)", None),
                         lambda yes: self.delete_files(rows) if yes else None)

    def delete_files(self, rows: list) -> None:
        from bdnex.library import twins

        done, failed = [], []
        for row in rows:
            try:
                entry = twins.trash_file(self.library, self.settings, row.path, "deleted in the review screen")
            except Exception as err:
                failed.append(f"{os.path.basename(row.path)}: {err}")
                continue
            done.append((row, entry, row.status))
            row.status = "trashed"
            self.marked.discard(id(row))
            self.refresh_row(row)
        if done:
            self.undo_stack = [*getattr(self, "undo_stack", []), ("delete", done)]
            self.refresh_album_rows({r.album_id for r, _, _ in done if r.album_id is not None})
            self.schedule_counts()
            self.notify(f"{len(done)} file(s) in the trash. Undo: U, or Actions, Undo.", timeout=6)
        if failed:
            self.notify("Not deleted: " + "; ".join(failed[:3]), severity="error", timeout=10)

    def undo_label(self) -> str:
        """What the Undo entry would take back: the last Delete or Move done in this session (else the last move run of the library)."""
        stack = getattr(self, "undo_stack", [])
        if not stack:
            return "Undo the last move"
        kind, what = stack[-1]
        return f"Undo: delete ({len(what)} file(s))" if kind == "delete" else "Undo: move (the last run)"

    def action_undo(self) -> None:
        """One undo, as in any program: the last thing done in this session (a Delete or a Move), else the last move run of the library."""
        stack = getattr(self, "undo_stack", [])
        if isinstance(self.screen, (ActionsScreen, TaskScreen)) or getattr(self, "organising", False):
            return
        if not stack:
            self.push_screen(TaskScreen("undo"), self.task_closed)  # nothing done here: the last move run, if there is one
            return
        kind, what = stack.pop()
        if kind == "move":
            self.push_screen(TaskScreen("undo", what), self.task_closed)
        else:
            self.undo_delete(what)

    def undo_delete(self, group) -> None:
        from bdnex import trash

        back, failed = 0, []
        for row, entry, status in group:
            try:
                path = trash.restore(self.library, entry, self.settings)
            except Exception as err:
                failed.append(f"{os.path.basename(row.path)}: {err}")
                continue
            if path is None:
                failed.append(f"{os.path.basename(row.path)}: no longer in the trash folder")
                continue
            row.path, row.status = path, status
            back += 1
            self.refresh_row(row)
        self.refresh_album_rows({r.album_id for r, _, _ in group if r.album_id is not None})
        self.schedule_counts()
        if back:
            self.notify(f"{back} file(s) put back.", timeout=4)
        if failed:
            self.notify("Not put back: " + "; ".join(failed[:3]), severity="error", timeout=10)
