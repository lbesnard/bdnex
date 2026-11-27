"""The Actions panel of the review screen: a list that slides in from the left, one click for what used to be a command line.

``A`` (or the ``☰ Actions`` chip at the top) opens it. Write, Move and Repair open their window (``review_organise``) for the view or the
whole collection. The other entries run the command of the command line (``update``, ``undo``, ``check``, ``trash``): the same ``run(argv, out)``
function in a worker thread, its output in a window, a preview first for the ones that have one. No second copy of any logic.
"""

from __future__ import annotations

import importlib
import io
import threading

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.css.scalar import ScalarOffset
from textual.geometry import Offset
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Label, Static

from bdnex.ui.review_modal import Modal

# (group, ((id, label, tooltip), ...))
ENTRIES = (
    ("WRITE the tags", (("write:view", "This view", "`bdnex write` for the files of the view you are looking at (key W)."),
                        ("write:all", "The whole collection", "`bdnex write` for every file of the library."))),
    ("MOVE the files", (("move:view", "This view", "`bdnex move` for the files of the view (key M)."),
                        ("move:all", "The whole collection", "`bdnex move` for every file of the library."))),
    ("REPAIR the damaged files", (("repair:view", "This view", "`bdnex repair` for the damaged files of the view (key H)."),
                                  ("repair:all", "The whole collection", "`bdnex repair` for every damaged file of the library."))),
    ("CHECK", (("quality", "Measure the quality (dpi)", "`bdnex check --quality`: the files that have no resolution yet. Then the Quality filter finds the low ones."),
               ("matches", "Weigh the matches", "`bdnex check --matches`: every match against its album's page; lists the ones that look wrong. Changes nothing."))),
    ("LIBRARY", (("update", "Read the changed files again", "`bdnex update`: files that another tool changed (size, checksum, pages, damage). A preview first."),
                 ("trash", "The trash", "`bdnex trash`: the files that were replaced. `bdnex trash --restore ID` puts one back."),
                 ("undo", "Undo the last move", "`bdnex undo last`: moves the files of the last `move` run back. A preview first."))),
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
    "undo": ("Undo the last move", "Moves the files of the last `move` run back to where they were.", ["last", "-n"], ["last"], "undo_cmd", True),
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
                    yield Button(label, id="act-" + entry.replace(":", "-"), name=entry, tooltip=tip)

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
    #taskbuttons { height: 3; align-horizontal: right; }
    #taskbuttons Button { margin-left: 2; }
    """

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.title_, self.what, self.preview_argv, self.run_argv, self.module, self.writes = TASKS[key]
        self.stage = "preview" if self.preview_argv else "ready"  # preview -> ready -> running -> done
        self.out = io.StringIO()
        self.lock = threading.Lock()
        self.ran = False

    def compose(self) -> ComposeResult:
        with Vertical(id="task"):
            yield Label(Text(self.title_, style="bold"))
            yield Label(Text(self.what, style="dim"))
            with VerticalScroll(id="taskbody"):
                yield Static("Working out what would change (nothing is touched)..." if self.preview_argv else "", id="tasktext")
            with Vertical(id="taskbuttons"):
                yield Button("Do it", id="go", variant="success", disabled=bool(self.preview_argv) or not self.run_argv and self.key == "trash")
                yield Button("Cancel", id="cancel", tooltip="Close (Esc).")

    def on_mount(self) -> None:
        if self.key == "trash":  # a list: it runs at once, there is nothing to confirm
            self.query_one("#go", Button).display = False
            self.begin(self.run_argv, final=True)
        elif self.preview_argv:
            self.begin(self.preview_argv, final=False)
        else:
            self.query_one("#go", Button).focus()
        self.set_interval(0.5, self.show_output)

    def begin(self, argv: list[str], final: bool) -> None:
        with self.lock:
            self.out = io.StringIO()
        if final:
            self.stage = "running"
            self.query_one("#cancel", Button).disabled = True
            self.ran = self.writes
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

    def show_output(self) -> None:
        with self.lock:
            text = self.out.getvalue()
        if text:
            lines = text.replace("\r", "\n").splitlines()
            self.query_one("#tasktext", Static).update(Text("\n".join(lines[-400:])))
            self.query_one("#taskbody", VerticalScroll).scroll_end(animate=False)

    def finished(self, final: bool, code) -> None:
        self.show_output()
        go, cancel = self.query_one("#go", Button), self.query_one("#cancel", Button)
        if final:
            self.stage = "done"
            go.display = False
            cancel.label, cancel.disabled = "Close", False
            cancel.focus()
        else:
            self.stage = "ready"
            go.disabled = bool(code)
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
        if ":" in chosen:
            mode, scope = chosen.split(":")
            self.organise_open(mode, scope)
        elif chosen == "help":
            self.action_help()
        else:
            self.push_screen(TaskScreen(chosen), self.task_closed)

    def task_closed(self, ran) -> None:
        if ran:
            self.organise_refresh()
