"""What the folder tree, the duplicate screen and the bulk form add to ``ReviewApp`` (a mixin: the screen itself stays about one batch).

Every write goes through the library modules (``folders``, ``twins``, ``bulk``) with the screen's own library, like the other decisions;
the tree's counts are read in a worker with its own read-only library.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Optional

from textual.widgets import Button

from bdnex.library import review_batch as batch
from bdnex.ui import review_panels as panels
from bdnex.ui.review_panels import TickTree

logger = logging.getLogger(__name__)


class ReviewExtras:
    """Keys: ``f`` the folder tree on or off, ``R`` the folder is reviewed, ``D`` the duplicate screen, ``E`` one form for the marked files,
    ``K`` series or one-shot, ``V`` the volume of this file."""

    folder_root = None
    folder_tree = None
    facet_tree = None
    move_tree = None
    move_root = None
    planned: Optional[dict] = None  # {file id: where a move would put it}, known a few seconds after the screen opens
    plan_dirty: set = set()  # albums changed since: their destinations are worked out again
    facet_choices: list = []  # [(group, value, files)] for the command palette
    folder_bad: dict = {}
    folder_current = ""
    counts_due = False

    # -- the layout as it was left (sizes of the panes dragged with the mouse, the tab of the left panel)
    def restore_layout(self) -> None:
        sizes = self.view_extra.get("sizes") or {}
        with contextlib.suppress(Exception):
            if sizes.get("folderpane"):
                self.query_one("#folderpane").styles.width = int(sizes["folderpane"])
            if sizes.get("facetbox"):
                self.query_one("#facetbox").styles.height = int(sizes["facetbox"])
            if sizes.get("detailrow"):
                self.query_one("#detailrow").styles.height = int(sizes["detailrow"])
            if self.view_extra.get("tab") in ("tab-folders", "tab-moves"):
                self.query_one("#trees").active = self.view_extra["tab"]

    def on_splitter_moved(self, event) -> None:
        self.remember(sizes={"folderpane": self.query_one("#folderpane").size.width, "facetbox": self.query_one("#facetbox").size.height,
                             "detailrow": self.query_one("#detailrow").size.height})

    def on_tabbed_content_tab_activated(self, event) -> None:
        if event.pane is not None and event.pane.id in ("tab-folders", "tab-moves") and self.view_extra.get("tab") != event.pane.id:
            self.remember(tab=event.pane.id)

    # -- Ctrl+P: every action of the screen by name (a filter value is found by `FacetCommands`)
    def get_system_commands(self, screen):
        from textual.app import SystemCommand

        yield from super().get_system_commands(screen)
        for title, help_text, callback in (
                ("Folders: show or hide the left panel", "the folders, the organised folders, the filters (f)", self.action_folders),
                ("Folder: mark it reviewed", "every file of the folder on the screen (R)", self.action_reviewed_folder),
                ("Duplicates: the other files of this album", "trash, make main, keep as a version (D)", self.action_duplicates),
                ("Edit the marked files", "one form: fields, kind, a volume each (E)", self.action_bulk),
                ("Series or one-shot", "the marked files, or this one (K)", self.action_kind),
                ("Volume of this file", "type it, Enter goes to the next (V)", self.action_volume),
                ("Mark every file", "or none, when all are marked (m)", self.action_mark_all),
                ("Keep this match", "the flag goes (k)", self.action_keep),
                ("Unlink this file", "it is asked about again by `bdnex import --skipped` (u)", self.action_unlink),
                ("Another album: search", "the whole album list (o)", self.action_other),
                ("Covers: the file's and the album's", "side by side (c)", self.action_cover),
                ("Everything: all the files", "the first thousand, in path order", lambda: self.open_selection("folder", "", "the collection"))):
            yield SystemCommand(title, help_text, callback)

    # -- the left panel: the folders as they are, the organised folders, and the filters
    def want_tree(self) -> bool:
        if self.tree_pref is not None:
            return self.tree_pref
        return bool(self.view_extra.get("tree", True))

    def remember_tree(self, shown: bool) -> None:
        self.remember(tree=shown)

    def start_folders(self) -> None:
        self.plan_dirty, self.facet_choices, self.folder_bad = set(), [], {}
        self.query_one("#folderpane").display = self.want_tree()
        self.run_worker(self.read_folders, thread=True, group="folders", exclusive=True)

    def read_folders(self) -> None:
        """In a worker with its own read-only library: the counts of the folders and of the filters (fast: indexes), then where a move would put
        every file (about two seconds for 17 000 files), or, after an edit, only the albums that changed."""
        from bdnex.library import Library, browse, folders

        dirty, self.plan_dirty = set(self.plan_dirty), set()
        try:
            with Library.for_settings(self.settings, readonly=True) as library:
                root = folders.build(folders.progress(library))
                bad = folders.inconsistent(library)
                found = browse.facets(library)
                with contextlib.suppress(Exception):
                    self.call_from_thread(self.folders_ready, root, bad, found)
                if self.planned is None or dirty:
                    planned = browse.plan(library, self.settings, only=dirty or None)
                else:
                    planned = {}
                checked = browse.checked_ids(library)
                move_root = None
                if self.planned is not None or planned:
                    merged = {**(self.planned or {}), **planned}
                    move_root = folders.build(browse.plan_counts(merged, checked))
        except Exception as err:
            logger.info("the folder tree could not be read: %s", err)
            return
        with contextlib.suppress(Exception):
            self.call_from_thread(self.plan_ready, planned, move_root, dirty)

    def folders_ready(self, root, bad: dict, found: dict) -> None:
        self.folder_root, self.folder_bad = root, bad
        self.facet_choices = [(group, value, count) for group, values in found.items() for value, count in values]
        pane = self.query_one("#folderpane")
        if self.folder_tree is None:
            for waiting in ("#foldersloading", "#facetwait"):
                with contextlib.suppress(Exception):
                    self.query_one(waiting).remove()
            self.folder_tree = panels.FolderTree(root, id="foldertree")
            self.query_one("#folderslot").mount(self.folder_tree)
            self.facet_tree = panels.FacetTree(found, id="facettree")
            self.query_one("#facetslot").mount(self.facet_tree)
        else:
            self.folder_tree.refresh_counts(root)
            self.facet_tree.fill(found)
        self.counts_due = False
        self.update_title()
        pane.refresh()

    def plan_ready(self, planned: dict, move_root, dirty: set) -> None:
        """Where a move would put the files is known (or known again for the albums that changed): the second tree, and the *Would go to* of the rows."""
        if self.planned is None:
            self.planned = {}
        if dirty:
            gone = {file_id for file_id, album in self.file_albums(self.planned).items() if album in dirty}
            for file_id in gone:
                self.planned.pop(file_id, None)
        self.planned.update(planned)
        if move_root is not None:
            self.move_root = move_root
            slot = self.query_one("#moveslot")
            if self.move_tree is None:
                self.move_tree = panels.FolderTree(move_root, id="movetree")
                slot.mount(self.move_tree)
                with contextlib.suppress(Exception):
                    self.query_one("#movewait").remove()
            else:
                self.move_tree.refresh_counts(move_root)
        for row in self.rows:
            if row.file_id in self.planned and (not row.dest or dirty):
                row.dest = self.planned[row.file_id]
                self.refresh_row(row)

    def file_albums(self, planned: dict) -> dict:
        """``{file id: album id}`` for the planned files, from the screen's own library (a cheap lookup: only used after an edit)."""
        ids = list(planned)
        found = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            for row in self.library.conn.execute(f"SELECT id, album_id FROM files WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                found[row["id"]] = row["album_id"]
        return found

    def schedule_counts(self) -> None:
        """A decision changed a file's state: the counts are read again in a moment (once, however many decisions come)."""
        if self.folder_tree is not None and not self.counts_due:
            self.counts_due = True
            self.set_timer(1.5, lambda: self.run_worker(self.read_folders, thread=True, group="folders", exclusive=True))

    def progress_text(self) -> str:
        root = self.folder_root
        return f" · reviewed {root.below_checked}/{root.total}" if root is not None and root.total else ""

    def action_folders(self) -> None:
        pane = self.query_one("#folderpane")
        pane.display = not pane.display
        self.remember_tree(pane.display)
        table = self.query_one("DataTable")
        keep = table.cursor_row

        def redraw() -> None:  # the table has more or less room now: its last column takes what is left
            if self.rows:
                self.render_table()
                table.move_cursor(row=min(keep, len(self.rows) - 1))

        self.call_after_refresh(redraw)
        if pane.display and self.folder_tree is not None:
            self.folder_tree.focus()
        else:
            table.focus()

    def on_tree_node_selected(self, event) -> None:
        """A folder (either tree), a letter, or a filter value: all its files, everything below it, in the table."""
        data = event.node.data
        tree_id = getattr(event.control, "id", "")
        if data is None:
            return
        if tree_id == "facettree":
            if isinstance(data, tuple) and data[0] == "facet":
                self.open_selection("facet", data[1], f"{data[1][0]}: {data[1][1]}")
            elif isinstance(data, tuple) and data[0] == "letter":  # a letter: every value under it (alternatives of one group)
                leaves = event.control.leaves(event.node)
                if leaves:
                    group = leaves[0][1][0]
                    self.open_selections([("facet", leaf[1]) for leaf in leaves], f"{group}: {event.node.label.plain.split('  ')[0]}")
            return
        kind = "planned" if tree_id == "movetree" else "folder"
        if getattr(data, "group", ""):  # a group of letters: all the folders it holds
            self.open_selections([(kind, child.path) for child in data.children.values()], f"{data.group}: {len(data.children)} folders")
            return
        self.open_selection(kind, data.path, f"{'organised: ' if kind == 'planned' else ''}{data.path or 'the collection'}")

    def ticked_selections(self) -> list:
        return [sel for tree in (self.folder_tree, self.move_tree, self.facet_tree) if tree is not None for sel in tree.selections()]

    def on_tick_tree_ticked(self, event: TickTree.Ticked) -> None:  # (a mixin: no `@on`, which only a message pump's own class collects)
        count = len(self.ticked_selections())
        self.query_one("#show-ticked", Button).label = f"Show ticked ({count})" if count else "Show ticked"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "show-ticked":
            self.action_show_ticked()
        elif event.button.id == "clear-ticks":
            self.action_clear_ticks()

    def action_show_ticked(self) -> None:
        picks = self.ticked_selections()
        if not picks:
            self.notify("Nothing ticked: space (or ctrl-click) ticks a folder or a filter.", timeout=4)
            return
        self.open_selections(picks, f"{len(picks)} ticked")

    def action_clear_ticks(self) -> None:
        for tree in (self.folder_tree, self.move_tree, self.facet_tree):
            if tree is not None:
                tree.clear_ticks()

    def open_folder(self, folder: str) -> None:
        self.open_selection("folder", folder, folder or "the collection")

    def open_facet(self, group: str, value: str) -> None:
        self.open_selection("facet", (group, value), f"{group}: {value}")

    def open_selection(self, kind: str, value, title: str) -> None:
        self.open_selections([(kind, value)], title)

    def open_selections(self, picks: list, title: str) -> None:
        """The files of one or several selections: folders and ``organised`` folders are alternatives, a group's values are alternatives, different
        groups go together."""
        only = picks[0] if len(picks) == 1 else None
        self.folder_current = only[1] if only and only[0] == "folder" else ""
        self.say(f"Reading {title}...")
        self.run_worker(lambda: self.weigh_selection(picks, title), thread=True, group="folder", exclusive=True)

    def weigh_selection(self, picks: list, title: str) -> None:
        import time

        from bdnex.library import Library, browse

        started = time.monotonic()
        try:
            with Library.for_settings(self.settings, readonly=True) as library:
                rows = browse.rows_for_many(library, self.settings, picks, self.planned)
        except Exception as err:
            self.call_from_thread(self.show_error, str(err))
            return
        self.call_from_thread(self.show_folder, title, rows, time.monotonic() - started)

    def show_folder(self, title: str, rows, seconds: float) -> None:
        """The files of a selection as a batch of their own: ``b`` and ``n`` still go back and forth between the batches."""
        self.batches.append(rows)
        self.batch_totals.append(len(rows))
        self.at = len(self.batches) - 1
        self.number = self.at + 1
        self.display(rows, f"{title}: {len(rows)} file(s) in {seconds:.2f} s")
        below = getattr(rows, "total", len(rows))
        if below > len(rows):
            self.notify(f"{below} files: the first {len(rows)} are shown. Open a subfolder or add a filter for the others.", timeout=6)
        if title in self.folder_bad:
            self.notify(f"{title}: {self.folder_bad[title]}", severity="warning", timeout=6)

    def action_reviewed_folder(self) -> None:
        """Everything in the folder on the screen is looked at: marked reviewed (a flagged match is kept, nothing is moved or unlinked)."""
        from bdnex.library import folders

        if not self.rows:
            return
        folder = self.folder_current
        count = sum(1 for _ in folders.stored_files(self.library, folder, True))
        from bdnex.ui.review_tui import AskScreen

        def done(yes) -> None:
            if not yes:
                return
            marked = folders.mark_reviewed(self.library, folder, recursive=True)
            for row in self.rows:
                if not row.status and not row.busy:
                    row.status = "keep"
                    self.refresh_row(row)
            self.notify(f"{marked} file(s) marked reviewed", timeout=4)
            self.schedule_counts()

        self.push_screen(AskScreen(f"Mark the {count} file(s) of {folder or 'the collection'} as reviewed?", None), done)

    # -- duplicates
    def action_duplicates(self) -> None:
        from bdnex.library import twins

        row = self.idle()
        if row is None:
            return
        found = twins.twins_of(self.library, row.path)
        if not found:
            self.notify("This file is the only one of its album.", timeout=3)
            return
        lines = twins.describe(found, self.settings.duplicates.prefer)[len(found) * 2:]
        screen = panels.DuplicateScreen(row.album or "this album", found, [line for line in lines if line])
        self.push_screen(screen, lambda answer: self.resolve_duplicate(row, answer) if answer else None)

    def resolve_duplicate(self, row, answer) -> None:
        from bdnex.library import twins

        action, path, note = answer
        album_id = row.album_id
        try:
            if action == "trash":
                twins.trash_file(self.library, self.settings, path)
                message = f"{os.path.basename(path)} is in the trash (`bdnex trash --restore` brings it back)"
                for other in self.rows:
                    if other.path == path:
                        other.status = "trashed"
                        self.refresh_row(other)
            elif action == "main":
                twins.make_main(self.library, path)
                message = f"{os.path.basename(path)} is the main file now"
            elif action in ("version", "child"):
                new = twins.keep_as_version(self.library, path, action, note)
                message = f"{os.path.basename(path)} has an album of its own now (album {new}), a {action} of the first"
                for other in self.rows:
                    if other.path == path:
                        batch.record_manual(self.library, other, new)
            else:  # relink: the file belongs to another album
                for index, other in enumerate(self.rows):
                    if other.path == path:
                        self.query_one("DataTable").move_cursor(row=index)
                        self.action_other()
                        return
                self.notify("Open that file's folder, then `o` links it to another album.", timeout=5)
                return
        except Exception as err:
            self.notify(f"Not done: {err}", severity="error", timeout=8)
            return
        self.refresh_album_rows({album_id} if album_id is not None else set())
        for other in self.rows:
            self.refresh_dest(other)
            self.refresh_row(other)
        self.notify(message, timeout=6)
        self.schedule_counts()

    # -- many files, one form
    def action_bulk(self) -> None:
        from bdnex.library import bulk, manual

        rows = [r for r in self.rows if id(r) in self.marked and not r.busy] or ([self.current()] if self.current() else [])
        if not rows:
            return
        shared = bulk.shared(self.library, [r.album_id for r in rows])
        fields = [f for f in manual.FIELDS if f[0] in (*manual.SHARED, "category", "subseries", "cycle", "alternate_number")]
        files = []
        for r in rows:
            album = self.library.get_album(r.album_id) if r.album_id is not None else None
            files.append((r.path, album.get("volume") if album else None, f"[{album['kind']}]" if album else "[no album]"))
        heading = f"{len(rows)} file(s): set what they share, the kind, a volume each"
        self.push_screen(panels.BulkScreen(heading, shared, fields, files), lambda got: self.apply_bulk(rows, got) if got else None)

    def apply_bulk(self, rows, got: dict) -> None:
        from bdnex.library import bulk

        targets = [bulk.Target(r.path, r.album_id) for r in rows]
        try:
            out = bulk.apply(self.library, targets, got["values"], got["kind"], got["volumes"])
        except Exception as err:
            self.notify(f"Not saved ({err}): nothing was changed", severity="error", timeout=8)
            return
        if not out.ok:
            self.notify("Nothing written: " + "; ".join(out.problems[:3]), severity="error", timeout=10)
            return
        for row, target in zip(rows, targets):
            if row.album_id is None and target.album_id is not None:
                self.supersede(row)
                batch.record_manual(self.library, row, target.album_id)
            else:
                row.status = row.status or "keep"
        self.refresh_album_rows({t.album_id for t in targets if t.album_id is not None})
        for row in rows:
            self.marked.discard(id(row))
            self.refresh_dest(row)
            self.refresh_row(row)
        self.notify(f"{out.changed} album(s) changed, {out.created} written by hand, {out.marked} file(s) reviewed", timeout=6)
        self.schedule_counts()

    def action_kind(self) -> None:
        """Series or one-shot for the marked files (or the one under the cursor): a one-shot becomes a series (asked for its name), a series
        album a one-shot."""
        from bdnex.library import bulk
        from bdnex.ui.review_tui import AskScreen

        rows = [r for r in self.rows if id(r) in self.marked and not r.busy] or ([self.current()] if self.current() else [])
        albums = [self.library.get_album(r.album_id) for r in rows if r.album_id is not None]
        if not albums:
            self.notify("No album to change: link the file first (`o`), or `E` writes one by hand.", timeout=4)
            return
        if all(a["kind"] == "series" for a in albums):
            self.apply_bulk(rows, {"values": {}, "kind": bulk.ONESHOT, "volumes": {}})
            return
        guess = os.path.basename(os.path.dirname(rows[0].path))
        self.push_screen(AskScreen(f"Series name for {len(rows)} file(s)?", guess),
                         lambda name: self.apply_bulk(rows, {"values": {"series": name}, "kind": bulk.SERIES, "volumes": {}}) if name else None)

    def action_volume(self) -> None:
        """The volume of the file under the cursor, typed in a line; Enter saves it and moves to the next file."""
        from bdnex.ui.review_tui import AskScreen

        row = self.idle()
        if row is None or row.album_id is None:
            self.notify("This file has no album yet.", timeout=3)
            return
        album = self.library.get_album(row.album_id) or {}
        current = "" if album.get("volume") is None else str(album["volume"])

        def done(text) -> None:
            if text is None:
                return
            if text != "-" and not (text.isascii() and text.isdigit()):
                self.notify("A volume is a number (`-` empties it).", severity="error")
                return
            volume = None if text == "-" else int(text)
            self.apply_bulk([row], {"values": {}, "kind": None, "volumes": {row.path: volume}})
            table = self.query_one("DataTable")
            if table.cursor_row + 1 < len(self.rows):
                table.move_cursor(row=table.cursor_row + 1)

        self.push_screen(AskScreen(f"Volume of {os.path.basename(row.path)} (now {current or 'none'})", current), done)
