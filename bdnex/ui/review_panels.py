"""The extra panels of ``bdnex review --batch``: the folder tree, the duplicate screen, the form for many files.

Each is a thin widget over a library module (``library/folders``, ``library/twins``, ``library/bulk``): the screen shows, the library decides.
"""

from __future__ import annotations

import os
from functools import partial
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Provider
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea, Tree

from bdnex.library import folders

STATUS_STYLE = {folders.DONE: "green", folders.PARTIAL: "dark_orange", folders.TODO: "yellow", folders.PROBLEM: "red"}
STATUS_MARK = {folders.DONE: "●", folders.PARTIAL: "◐", folders.TODO: "○", folders.PROBLEM: "✖"}  # (the shape says it too: a colour is not the only sign)


def node_label(node: "folders.FolderNode") -> Text:
    """``● Tintin  41/60`` in the colour of how far it is reviewed; ``!3`` when 3 files wait for a look (no album, or a doubtful match)."""
    issues = f"  !{node.below_issues}" if node.below_issues else ""
    return Text(f"{STATUS_MARK[node.status]} {node.name or 'collection'}  {node.below_checked}/{node.total}{issues}", style=STATUS_STYLE[node.status])


class FolderTree(Tree):
    """The folders of the library, coloured by how far they are reviewed; selecting one asks the screen to show its files.

    Children are added when a folder is opened (a library of 6000 folders is not built at once); a folder with very many children shows
    them in groups by first letter."""

    def __init__(self, root: "folders.FolderNode", **kwargs) -> None:
        super().__init__(node_label(root), data=root, **kwargs)
        self.show_root = True
        self.guide_depth = 3
        self.fill(self.root, root)
        self.root.expand()

    def fill(self, tree_node, folder: "folders.FolderNode") -> None:
        tree_node.remove_children()
        for child in folder.shown_children():
            node = tree_node.add(node_label(child), data=child, allow_expand=bool(child.children))
            if child.children:
                node.data = child

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        if not node.children and node.data is not None and node.data.children:
            self.fill(node, node.data)

    def refresh_counts(self, root: "folders.FolderNode") -> None:
        """New counts from the library, the tree otherwise left as it is (what is expanded stays expanded)."""
        def walk(tree_node, folder: Optional["folders.FolderNode"]) -> None:
            if folder is None:
                return
            tree_node.data = folder
            tree_node.set_label(node_label(folder))
            by_name = {child.name: child for child in folder.shown_children()}
            for child in tree_node.children:
                walk(child, by_name.get(child.data.name if child.data is not None else None))

        walk(self.root, root)

    def select_path(self, folder: str) -> None:
        """Move the cursor to the node of ``folder`` when the tree has it open."""
        def search(tree_node) -> Optional[object]:
            data = tree_node.data
            if data is not None and not data.group and data.path == folder:
                return tree_node
            for child in tree_node.children:
                found = search(child)
                if found is not None:
                    return found
            return None

        node = search(self.root)
        if node is not None:
            self.move_cursor(node)


class FacetTree(Tree):
    """The filters: groups (state, kind, category, collection, genre, author, series...) and their values with the number of files.
    Selecting a value asks the screen to show its files. A group with very many values (authors, series) lists them by first letter."""

    def __init__(self, found: dict, **kwargs) -> None:
        super().__init__("Filters", **kwargs)
        self.show_root = False
        self.guide_depth = 3
        self.fill(found)

    def fill(self, found: dict) -> None:
        from bdnex.library import browse

        self.clear()
        described = dict(browse.STATES)
        for group in browse.GROUPS:
            values = found.get(group) or []
            if not values:
                continue
            node = self.root.add(Text(f"{group}  ({len(values)})" if group != "State" else group, style="bold"), allow_expand=True)
            if len(values) > folders.MANY:
                letters: dict[str, list] = {}
                for value, count in values:
                    letter = (value[:1] or "#").upper()
                    letters.setdefault(letter if letter.isalpha() else "#", []).append((value, count))
                for letter in sorted(letters):
                    sub = node.add(Text(f"{letter}  ({len(letters[letter])})", style="dim"), allow_expand=True)
                    for value, count in letters[letter]:
                        sub.add_leaf(Text(f"{value}  {count}"), data=("facet", (group, value)))
            else:
                for value, count in values:
                    label = Text(f"{value}  {count}")
                    if group == "State" and value in described:
                        label.append(f"   {described[value]}", style="dim")
                    node.add_leaf(label, data=("facet", (group, value)))
        self.root.expand()


class FacetCommands(Provider):
    """Ctrl+P: type a few letters of a filter value (`genre fantasy`, `author hergé`, `series tintin`, `state duplicates`): its files are shown."""

    async def search(self, query: str):
        matcher = self.matcher(query)
        for group, value, count in self.app.facet_choices:
            text = f"{group}: {value}"
            score = matcher.match(text)
            if score > 0:
                yield Hit(score, matcher.highlight(text), partial(self.app.open_facet, group, value), help=f"{count} file(s)")

    async def discover(self):
        for group, value, count in self.app.facet_choices:
            if group == "State":
                yield DiscoveryHit(f"State: {value}", partial(self.app.open_facet, group, value), help=f"{count} file(s)")


class DuplicateScreen(ModalScreen):
    """Two or more files hold one album: them side by side, and what each one is (the answer is a letter, the library does the rest).

    Dismisses with ``(action, path, extra)``: ``trash`` the file, ``main`` make it the main one, ``version`` / ``child`` give it an album of
    its own (``extra`` is the note, ``"first"`` flips who is the parent), ``relink`` (it belongs to another album), or ``None``."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", "Close", show=False),
                Binding("t", "choose('trash')", "Trash this file"), Binding("m", "choose('main')", "Make it the main file"),
                Binding("v", "choose('version')", "A different version"), Binding("c", "choose('child')", "A child of the album"),
                Binding("r", "choose('relink')", "Wrong album: link elsewhere"), Binding("b", "close", "Keep both as they are"),
                Binding("down", "step(1)", show=False, priority=True), Binding("up", "step(-1)", show=False, priority=True)]
    CSS = """
    DuplicateScreen { align: center middle; }
    #dup { width: 92%; height: auto; max-height: 92%; border: round $accent; padding: 1 2; background: $surface; }
    #dupfiles { height: auto; margin-bottom: 1; }
    #dupnote { height: 3; }
    """

    def __init__(self, album: str, found: list, lines: list[str], keys_hint: str = "") -> None:
        super().__init__()
        self.album, self.found, self.lines, self.at = album, found, lines, 0
        self.asking: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="dup"):
            yield Label(Text(f"{len(self.found)} files for  {self.album}", style="bold"))
            yield Static("", id="dupfiles")
            yield Static("", id="dupverdict")
            yield Label(Text("Note for a version (N&B, 1st edition...), then Enter:", style="dim"), id="dupask")
            yield Input(value="", id="dupnote", placeholder="N&B")
            yield Label(Text("↑↓ pick a file · [T]rash it (restorable) · [M]ake it the main file · [V]ersion: it keeps its own album · "
                             "[C]hild · [R] wrong album: link it elsewhere · [B]oth stay · Esc closes", style="dim"))

    def on_mount(self) -> None:
        self.query_one("#dupask").display = False
        note = self.query_one("#dupnote", Input)
        note.display = False
        note.disabled = True  # (a hidden box would still take the focus, and the letters typed for the keys)
        self.set_focus(None)
        self.draw()

    def draw(self) -> None:
        text = Text()
        for index, twin in enumerate(self.found):
            lines = self.lines_of(twin)
            text.append(("▶ " if index == self.at else "  ") + lines[0] + "\n", style="bold reverse" if index == self.at else "bold")
            text.append("  " + lines[1] + "\n")
        self.query_one("#dupfiles", Static).update(text)
        self.query_one("#dupverdict", Static).update(Text("\n".join(self.lines), style="yellow"))

    @staticmethod
    def lines_of(twin) -> list[str]:
        from bdnex.library import twins

        return twins.facts_lines(twin)

    def action_step(self, step: int) -> None:
        if self.asking is None:
            self.at = (self.at + step) % len(self.found)
            self.draw()

    def action_close(self) -> None:
        if self.asking is not None:
            self.asking = None
            self.query_one("#dupask").display = False
            note = self.query_one("#dupnote", Input)
            note.display, note.disabled = False, True
            self.set_focus(None)
            return
        self.dismiss(None)

    def action_choose(self, action: str) -> None:
        if self.asking is not None:
            return
        path = self.found[self.at].path
        if action in ("version", "child"):
            self.asking = action  # a note is asked first (it tells the two albums apart)
            self.query_one("#dupask").display = True
            box = self.query_one("#dupnote", Input)
            box.disabled, box.display = False, True
            box.focus()
            return
        self.dismiss((action, path, ""))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.asking:
            self.dismiss((self.asking, self.found[self.at].path, event.value.strip()))


class BulkScreen(ModalScreen):
    """One form for the marked files: the common fields (their shared value is shown; what you type is set on all of them), the kind
    (series or one-shot), and a volume for each file.

    Dismisses with ``{"values": {field: text}, "kind": None | "series" | "oneshot", "volumes": {path: int | None}}`` (only the fields
    that were changed), or ``None``."""

    BINDINGS = [Binding("escape", "back", "Cancel"), Binding("ctrl+s", "accept", "Save")]
    CSS = """
    BulkScreen { align: center middle; }
    #bulk { width: 96%; height: 96%; border: round $accent; padding: 0 2; background: $surface; }
    #bulkfields { height: 1fr; overflow-y: auto; }
    .fieldrow { height: 3; }
    .fieldname { width: 18; padding-top: 1; color: $text-muted; }
    .fieldrow Input { width: 1fr; }
    #bulkkind { width: 30; }
    #bulkvolumes { height: 9; }
    #bulkproblem { color: $error; height: auto; }
    #bulkbuttons { height: 3; align-horizontal: right; }
    #bulkbuttons Button { margin-left: 2; }
    """

    KINDS = [("leave as they are", ""), ("series (named above)", "series"), ("one-shots", "oneshot")]

    def __init__(self, heading: str, shared: dict, fields, files: list[tuple[str, Optional[int], str]]) -> None:
        """``fields``: ``manual.FIELDS``-like rows ``(key, label, hint)``; ``shared``: what the albums agree on (``None``: they differ);
        ``files``: ``(path, volume, note)`` for the volume list."""
        super().__init__()
        self.heading, self.shared, self.fields, self.files = heading, shared, fields, files

    def compose(self) -> ComposeResult:
        with Vertical(id="bulk"):
            yield Label(Text(self.heading, style="bold"))
            yield Label(Text("A field you leave as it is stays as it is on every file; one you type is set on all of them; "
                             "`-` empties it. The grey text is what they already have in common.", style="dim"))
            with Vertical(id="bulkfields"):
                with Horizontal(classes="fieldrow"):
                    yield Label("Kind", classes="fieldname")
                    yield Select(self.KINDS, value="", allow_blank=False, id="bulkkind")
                for key, label, hint in self.fields:
                    shared = self.shared.get(key)
                    holder = "(they differ)" if shared is None else (shared or hint)
                    with Horizontal(classes="fieldrow"):
                        yield Label(label, classes="fieldname")
                        yield Input(value="", placeholder=holder, id=f"b-{key}")
            yield Label(Text("Volume and file (one line each: change a number; [N]umber from 1, or from the file names with the buttons)",
                             style="dim"))
            yield TextArea("\n".join(f"{'' if v is None else v}\t{os.path.basename(p)}  {note}" for p, v, note in self.files), id="bulkvolumes")
            yield Label("", id="bulkproblem")
            with Horizontal(id="bulkbuttons"):
                yield Button("Number 1..n", id="number")
                yield Button("From the names", id="names")
                yield Button("Save", id="ok", variant="success")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(f"#b-{self.fields[0][0]}", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "ok":
            self.action_accept()
        elif event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "number":
            self.set_volumes([str(n) for n in range(1, len(self.files) + 1)])
        elif event.button.id == "names":
            from bdnex.library import manual

            self.set_volumes([str(manual.guess(path)["volume"]) for path, _, _ in self.files])

    def set_volumes(self, numbers: list[str]) -> None:
        self.query_one("#bulkvolumes", TextArea).text = "\n".join(
            f"{number}\t{os.path.basename(path)}  {note}" for number, (path, _, note) in zip(numbers, self.files))

    def action_back(self) -> None:
        self.dismiss(None)

    def action_accept(self) -> None:
        values = {}
        for key, _, _ in self.fields:
            typed = self.query_one(f"#b-{key}", Input).value.strip()
            if typed:
                values[key] = "" if typed == "-" else typed
        lines = self.query_one("#bulkvolumes", TextArea).text.split("\n")
        while len(lines) > len(self.files) and not lines[-1].strip():
            lines.pop()
        if len(lines) != len(self.files):
            self.query_one("#bulkproblem", Label).update("keep one line per file")
            return
        volumes: dict[str, Optional[int]] = {}
        for (path, old, _), line in zip(self.files, lines):
            first = line.split("\t", 1)[0].strip()
            if first and not (first.isascii() and first.isdigit()):
                self.query_one("#bulkproblem", Label).update(f"volume must be a number: {first!r}")
                return
            volume = int(first) if first else None
            if volume != old:
                volumes[path] = volume  # only the files whose number was changed
        kind = self.query_one("#bulkkind", Select).value or None
        self.dismiss({"values": values, "kind": kind or None, "volumes": volumes})
