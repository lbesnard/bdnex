"""What the folder tree, the duplicate screen and the bulk form add to ``ReviewApp`` (a mixin: the screen itself stays about one batch).

Every write goes through the library modules (``folders``, ``twins``, ``bulk``) with the screen's own library, like the other decisions;
the tree's counts are read in a worker with its own read-only library.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os

from bdnex.library import review_batch as batch
from bdnex.ui import review_panels as panels

logger = logging.getLogger(__name__)


def preference_file(settings, library) -> str:
    return str(getattr(library, "path", "") or "") + ".review-ui.json"


class ReviewExtras:
    """Keys: ``f`` the folder tree on or off, ``R`` the folder is reviewed, ``D`` the duplicate screen, ``E`` one form for the marked files,
    ``K`` series or one-shot, ``V`` the volume of this file."""

    folder_root = None
    folder_tree = None
    folder_bad: dict = {}
    folder_current = ""
    counts_due = False

    # -- the tree
    def want_tree(self) -> bool:
        if self.tree_pref is not None:
            return self.tree_pref
        with contextlib.suppress(Exception):
            with open(preference_file(self.settings, self.library), encoding="utf-8") as handle:
                return bool(json.load(handle).get("tree", True))
        return True

    def remember_tree(self, shown: bool) -> None:
        with contextlib.suppress(Exception):
            if self.library.path != ":memory:":
                with open(preference_file(self.settings, self.library), "w", encoding="utf-8") as handle:
                    json.dump({"tree": shown}, handle)

    def start_folders(self) -> None:
        pane = self.query_one("#folderpane")
        pane.display = self.want_tree()
        self.run_worker(self.read_folders, thread=True, group="folders", exclusive=True)

    def read_folders(self) -> None:
        from bdnex.library import Library, folders

        try:
            with Library.for_settings(self.settings, readonly=True) as library:
                root = folders.build(folders.progress(library))
                bad = folders.inconsistent(library)
        except Exception as err:
            logger.info("the folder tree could not be read: %s", err)
            return
        with contextlib.suppress(Exception):
            self.call_from_thread(self.folders_ready, root, bad)

    def folders_ready(self, root, bad: dict) -> None:
        self.folder_root, self.folder_bad = root, bad
        pane = self.query_one("#folderpane")
        if self.folder_tree is None:
            pane.remove_children()
            self.folder_tree = panels.FolderTree(root, id="foldertree")
            pane.mount(self.folder_tree)
        else:
            self.folder_tree.refresh_counts(root)
        self.counts_due = False
        self.update_title()

    def schedule_counts(self) -> None:
        """A decision changed a file's state: the tree's counts are read again in a moment (once, however many decisions come)."""
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
            self.query_one("DataTable").focus()

    def on_tree_node_selected(self, event) -> None:
        folder = event.node.data
        if folder is None or getattr(folder, "group", ""):
            return
        self.open_folder(folder.path)

    def open_folder(self, folder: str) -> None:
        self.folder_current = folder
        self.say(f"Reading {folder or 'the collection'}...")
        self.run_worker(lambda: self.weigh_folder(folder), thread=True, group="folder", exclusive=True)

    def weigh_folder(self, folder: str) -> None:
        import time

        from bdnex.library import Library

        started = time.monotonic()
        try:
            with Library.for_settings(self.settings, readonly=True) as library:
                rows = batch.folder_batch(library, self.settings, folder)
        except Exception as err:
            self.call_from_thread(self.show_error, str(err))
            return
        self.call_from_thread(self.show_folder, folder, rows, time.monotonic() - started)

    def show_folder(self, folder: str, rows, seconds: float) -> None:
        """A folder's files as a batch of their own: ``b`` and ``n`` still go back and forth between the batches."""
        self.batches.append(rows)
        self.batch_totals.append(len(rows))
        self.at = len(self.batches) - 1
        self.number = self.at + 1
        self.display(rows, f"{folder or 'the collection'}: {len(rows)} file(s) in {seconds:.1f} s")
        below = getattr(rows, "total", len(rows))
        if below > len(rows):
            self.notify(f"{below} files below: the folder's own {len(rows)} are shown, open a subfolder for the others.", timeout=6)
        if folder in self.folder_bad:
            self.notify(f"{folder}: {self.folder_bad[folder]}", severity="warning", timeout=6)

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
