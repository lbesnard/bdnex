"""Interactive prompts (InquirerPy) shared by the commands."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import webbrowser
from typing import Optional

from bdnex.importer.matching import Match, query_from_filename
from bdnex.importer.session import ImportAborted, NeedsTerminal
from bdnex.importer.source import Candidate
from bdnex.library.query import fold


def display_name(candidate: Candidate) -> str:
    """A readable title for a candidate: the words of its page address, or its normalised name."""
    slug = candidate.url.rsplit("/", 1)[-1]
    if slug.startswith("BD-") and slug.endswith(".html"):
        return re.sub(r"-\d+$", "", slug[3:-5]).replace("-", " ")
    return candidate.name


FUZZY_POOL = 2000  # albums offered to the Python fuzzy prompt: its engine is too slow for the whole list
OPEN_HINT = "Ctrl-O opens the highlighted album's page in your browser"


def open_page(value) -> bool:
    """Open the page of a candidate in the default browser. Anything else (Skip, Search...) is ignored."""
    url = getattr(value, "url", None)
    return bool(url) and webbrowser.open(url)


def bind_open(prompt) -> None:
    """Make Ctrl-O open the page of the highlighted choice, in a select / fuzzy prompt."""
    @prompt.register_kb("c-o")
    def _open(event):
        open_page(prompt.result_value)


class InquirerPrompter:
    SKIP = "__skip__"
    ABORT = "__abort__"
    SEARCH = "__search__"
    AGAIN = "__again__"
    BACK = "__back__"

    def __init__(self, source=None):
        """``source``: the metadata source, when the user may search its album list by hand."""
        self.source = source

    # -- thin wrappers over InquirerPy, so the logic around them can be tested
    def _select(self, message: str, choices: list):
        from InquirerPy import inquirer

        prompt = inquirer.select(message=message, choices=choices, instruction=f"({OPEN_HINT})")
        bind_open(prompt)
        return prompt.execute()

    def _fuzzy(self, message: str, choices: list):
        """Type to filter the choices (case-insensitive); Ctrl-C gives ``None``."""
        from InquirerPy import inquirer

        prompt = inquirer.fuzzy(message=message, choices=choices, instruction=f"({OPEN_HINT}; Ctrl-C goes back)",
                                raise_keyboard_interrupt=False, mandatory=False, max_height="70%")
        bind_open(prompt)
        return prompt.execute()

    def _text(self, message: str, default: str = "") -> str:
        from InquirerPy import inquirer

        return inquirer.text(message=message, default=default).execute()

    @staticmethod
    def _need_terminal() -> None:
        if not sys.stdin.isatty():
            raise NeedsTerminal("a question has to be asked, but bdnex is not running in a terminal")

    # -- import
    def choose_match(self, path: str, matches: list[Match]) -> Optional[Candidate]:
        self._need_terminal()

        choices = [{"name": f"{m.score:5.1f}  {m.candidate.url.rsplit('/', 1)[-1]}", "value": m.candidate}
                   for m in matches]
        if self.source is not None:
            choices.append({"name": "Search the album list by hand...", "value": self.SEARCH})
        choices.append({"name": "Skip this file", "value": self.SKIP})
        choices.append({"name": "Abort the import (what is decided is kept; run it again to continue)", "value": self.ABORT})
        while True:
            answer = self._select(f"Match for {path}", choices)
            if answer == self.ABORT:
                raise ImportAborted(path)
            if answer == self.SKIP:
                return None
            if answer == self.SEARCH:
                found = self.search(path)
                if found is not None:
                    return found
                continue  # nothing picked: back to the list
            return answer

    # -- searching the whole album list
    def search(self, path: str) -> Optional[Candidate]:
        """Let the user find the album by hand: ``fzf`` over the whole list when it is installed, else a
        search prompt. Case and accents never matter. ``None`` when nothing was picked."""
        start = fold(query_from_filename(path))
        if shutil.which("fzf") and hasattr(self.source, "catalogue"):
            return self._search_with_fzf(path, start)
        return self._search_by_prompt(start)

    def _search_with_fzf(self, path: str, start: str) -> Optional[Candidate]:
        catalogue = {str(c.source_id): c for c in self.source.catalogue()}
        lines = "\n".join(f"{display_name(c)}\t{c.source_id}\t{c.url}" for c in catalogue.values())
        # Ctrl-O opens the third field (the page address) with the default browser
        opener = f"{shlex.quote(sys.executable)} -m webbrowser {{3}}"
        header = f"{os.path.basename(path)}   (Enter picks the album, Ctrl-O opens its page, Esc goes back)"
        result = subprocess.run(
            ["fzf", "-i", "--query", start, "--delimiter", "\t", "--with-nth", "1", "--prompt", "album> ",
             "--header", header, "--bind", f"ctrl-o:execute-silent({opener})", "--layout=reverse", "--height=70%"],
            input=lines, stdout=subprocess.PIPE, text=True)
        if result.returncode != 0 or not result.stdout.strip():
            return None  # no match, or Esc / Ctrl-C
        return catalogue.get(result.stdout.rstrip("\n").split("\t")[1])

    def _search_by_prompt(self, start: str) -> Optional[Candidate]:
        """Without ``fzf``: say which words to look for, then filter the best hits as you type."""
        text = start
        while True:
            text = self._text("Words to look for in the album list (case and accents do not matter; "
                              "empty to go back):", text).strip()
            if not text:
                return None
            hits = [c for c, _ in self.source.candidates(text, limit=FUZZY_POOL)]
            if not hits:
                print(f"Nothing in the album list looks like {text!r}.")
                continue
            answer = self._fuzzy(f"{len(hits)} albums for {text!r}: type to filter, Enter picks",
                                 [{"name": display_name(c), "value": c} for c in hits])
            if answer is not None:
                return answer  # None (Ctrl-C): back to the words

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
