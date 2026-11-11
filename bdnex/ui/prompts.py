"""Interactive prompts (InquirerPy) shared by the commands."""

from __future__ import annotations

from typing import Optional

from bdnex.importer.source import Candidate
from bdnex.importer.matching import Match


class InquirerPrompter:
    SKIP = "__skip__"

    # -- import
    def choose_match(self, path: str, matches: list[Match]) -> Optional[Candidate]:
        from InquirerPy import inquirer

        choices = [{"name": f"{m.score:5.1f}  {m.candidate.url.rsplit('/', 1)[-1]}", "value": m.candidate}
                   for m in matches]
        choices.append({"name": "Skip this file", "value": self.SKIP})
        answer = inquirer.select(message=f"Match for {path}", choices=choices).execute()
        return None if answer == self.SKIP else answer

    def duplicate_action(self, path: str, others: list[str], what: str) -> str:
        from InquirerPy import inquirer

        return inquirer.select(
            message=f"{what}: {path}\n  already: {', '.join(others)}",
            choices=[{"name": "Skip", "value": "skip"},
                     {"name": "Keep both", "value": "keep_both"},
                     {"name": "Replace (the new file becomes the album's file)", "value": "replace"}],
        ).execute()

    # -- write / move
    def conflict(self, path: str, diff_lines: list[str]) -> str:
        from InquirerPy import inquirer

        print(f"\n{path}\nComicInfo.xml in the file differs from the database:")
        for line in diff_lines:
            print(f"  {line}")
        return inquirer.select(
            message="Which one wins?",
            choices=[{"name": "Database (overwrite the file's values)", "value": "database"},
                     {"name": "File (keep its ComicInfo.xml as it is)", "value": "file"},
                     {"name": "Skip this file", "value": "skip"}],
        ).execute()

    def confirm(self, message: str) -> bool:
        from InquirerPy import inquirer

        return bool(inquirer.confirm(message=message, default=False).execute())

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
