"""Interactive prompts (InquirerPy) shared by the commands."""

from __future__ import annotations

from typing import Optional

import os
import sys

from bdnex.importer.matching import Match
from bdnex.importer.session import NeedsTerminal
from bdnex.importer.source import Candidate


class InquirerPrompter:
    SKIP = "__skip__"

    @staticmethod
    def _need_terminal() -> None:
        if not sys.stdin.isatty():
            raise NeedsTerminal("a question has to be asked, but bdnex is not running in a terminal")

    # -- import
    def choose_match(self, path: str, matches: list[Match]) -> Optional[Candidate]:
        from InquirerPy import inquirer

        self._need_terminal()

        choices = [{"name": f"{m.score:5.1f}  {m.candidate.url.rsplit('/', 1)[-1]}", "value": m.candidate}
                   for m in matches]
        choices.append({"name": "Skip this file", "value": self.SKIP})
        answer = inquirer.select(message=f"Match for {path}", choices=choices).execute()
        return None if answer == self.SKIP else answer

    def duplicate_action(self, path: str, others: list[str], what: str) -> str:
        from InquirerPy import inquirer

        self._need_terminal()

        return inquirer.select(
            message=f"{what}: {path}\n  already: {', '.join(others)}",
            choices=[{"name": "Skip", "value": "skip"},
                     {"name": "Keep both", "value": "keep_both"},
                     {"name": "Replace (the new file becomes the album's file)", "value": "replace"}],
        ).execute()

    def resolve_duplicate(self, comparison, what: str, root=None) -> str:
        """Which file(s) to keep when an album already has one: ``skip`` | ``replace`` | ``keep_both``."""
        from InquirerPy import inquirer

        from bdnex.versions import KEEP_EXISTING, table

        self._need_terminal()
        intro = ("An identical file is already in the library." if what == "identical file"
                 else "This album already has a file in the library.")
        print(f"\n{comparison.new.name}\n{intro}\n{table(comparison, root)}")
        choices = [{"name": "Keep the existing file (the new one is not imported)", "value": "skip"},
                   {"name": "Keep the new file (the existing one goes to the trash)", "value": "replace"},
                   {"name": "Keep both (the new one becomes a spare version)", "value": "keep_both"}]
        suggested = "skip" if comparison.suggestion == KEEP_EXISTING else "replace"
        choices.sort(key=lambda choice: choice["value"] != suggested)
        choices[0]["name"] += "  <- suggested"
        return inquirer.select(message="Which one do you keep?", choices=choices).execute()

    def resolve_collision(self, comparison, root=None) -> str:
        """The destination name is taken: ``skip`` | ``replace`` | ``keep_both`` (as a spare version)."""
        from InquirerPy import inquirer

        from bdnex.versions import KEEP_EXISTING, table

        self._need_terminal()
        print(f"\n{comparison.new.name}\nThe destination is already taken by another file.\n{table(comparison, root)}")
        choices = [{"name": "Keep the existing file (the new one is left where it is)", "value": "skip"},
                   {"name": "Keep the new file (the existing one goes to the trash)", "value": "replace"},
                   {"name": "Keep both (the new one goes to the spare versions)", "value": "keep_both"}]
        suggested = "skip" if comparison.suggestion == KEEP_EXISTING else "replace"
        choices.sort(key=lambda choice: choice["value"] != suggested)
        choices[0]["name"] += "  <- suggested"
        return inquirer.select(message="Which one do you keep?", choices=choices).execute()

    def cover_mismatch(self, path: str, album: str, score: float, threshold: float) -> str:
        """The file's cover does not look like the album's: ``keep`` the match or ``unlink`` it."""
        from InquirerPy import inquirer

        self._need_terminal()
        return inquirer.select(
            message=f"The cover of {os.path.basename(path)} looks little like the cover of {album} "
                    f"({score:.0f}% < {threshold:.0f}%).",
            choices=[{"name": "Keep this match", "value": "keep"},
                     {"name": "Unlink it (it will be looked at again at the next import --force)", "value": "unlink"}],
        ).execute()

    def choose_placement(self, count: int, directory: str) -> str:
        """What to do with files imported from outside the library folder: ``move`` | ``copy`` | ``leave``."""
        from InquirerPy import inquirer

        return inquirer.select(
            message=f"{count} imported file(s) are outside your library folder ({directory}). What now?",
            choices=[{"name": "Move them into the library (tags are written, names follow your templates)", "value": "move"},
                     {"name": "Copy them into the library (the originals stay where they are)", "value": "copy"},
                     {"name": "Leave them where they are (`bdnex move staged:true` places them later)", "value": "leave"}],
        ).execute()

    # -- write / move
    def conflict(self, path: str, diff_lines: list[str]) -> str:
        from InquirerPy import inquirer

        from bdnex.ui import diffview
        from bdnex.ui.color import enabled

        print("\n" + diffview.conflict_text(path, diff_lines, enabled()))
        return inquirer.select(
            message="Which one wins?",
            choices=[{"name": "Database (overwrite the file's values)", "value": "database"},
                     {"name": "File (keep its ComicInfo.xml as it is)", "value": "file"},
                     {"name": "Skip this file", "value": "skip"}],
        ).execute()

    def confirm(self, message: str, default: bool = False) -> bool:
        from InquirerPy import inquirer

        return bool(inquirer.confirm(message=message, default=default).execute())

    def ask_value(self, field: str, label: str) -> Optional[str]:
        from InquirerPy import inquirer

        value = inquirer.text(message=f"`{field}` is empty for \"{label}\". Value (empty = skip the file):").execute()
        return value.strip() or None

    def collision(self, dest: str) -> str:
        from InquirerPy import inquirer

        return inquirer.select(
            message=f"Destination exists: {dest}",
            choices=[{"name": "Skip", "value": "skip"},
                     {"name": "Keep both (add a number)", "value": "keep_both"},
                     {"name": "Replace", "value": "replace"}],
        ).execute()

    def corrupted_action(self, path: str, problems: list[str], can_move: bool) -> str:
        from InquirerPy import inquirer

        print(f"\n{path}\nThe archive is damaged and could not be repaired:")
        for line in problems:
            print(f"  - {line}")
        choices = [{"name": "Leave it as it is", "value": "leave"}]
        if can_move:
            choices.append({"name": "Move it anyway (as it is, no tags written)", "value": "move"})
        return inquirer.select(message="What now?", choices=choices).execute()
