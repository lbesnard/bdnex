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
from bdnex.importer.session import GoBack, ImportAborted, NeedsTerminal
from bdnex.importer.source import Candidate
from bdnex.library.query import fold
from bdnex.ui import color, thumbnail


def display_name(candidate: Candidate) -> str:
    """A readable title for a candidate: the words of its page address, or its normalised name."""
    slug = candidate.url.rsplit("/", 1)[-1]
    if slug.startswith("BD-") and slug.endswith(".html"):
        return re.sub(r"-\d+$", "", slug[3:-5]).replace("-", " ")
    return candidate.name


FUZZY_POOL = 2000  # albums offered to the Python fuzzy prompt: its engine is too slow for the whole list
OPEN_HINT = "Ctrl-O opens the highlighted album's page in your browser"
MATCH_HINT = "Enter or a highlighted key chooses; O opens the highlighted album's page"
KEY_STYLE = "bold underline"  # the letter of a shortcut, in the colour of its line


def shortcut(letter: str, text: str, tone: str) -> list:
    """``[S]kip``: the brackets and the text in ``tone``, the letter underlined and bold (as beets does)."""
    return [(tone, "["), (f"{tone} {KEY_STYLE}".strip(), letter), (tone, "]" + text)]


def bind_shortcuts(prompt, keys: dict) -> None:
    """Let a key answer the question as Enter would: ``{key: value}`` (lower and upper case are both bound)."""
    for key, value in keys.items():
        def answer(event, value=value):
            prompt.status["answered"] = True
            prompt.status["result"] = next((c["name"] for c in prompt.content_control.choices if c["value"] is value), str(value))
            event.app.exit(result=value)

        for variant in {key, key.upper()}:
            prompt.register_kb(variant)(answer)


Fragments = list  # of (prompt_toolkit style, text)


def colorize_choices(prompt, fragments: list) -> None:
    """Draw the name of each choice in pieces of different colours.

    InquirerPy paints a whole choice in one colour, and turns a custom label back into a plain string,
    so the pieces are kept aside, in the order of the choices (``None`` leaves a choice as it is), and
    drawn by the two methods that build the rows. The pointer and the marker are still InquirerPy's.
    """
    control = prompt.content_control
    plain_hover, plain_normal = control._get_hover_text, control._get_normal_text

    def row(choice, plain, hovered):
        index = next((i for i, c in enumerate(control.choices) if c is choice), None)
        pieces = fragments[index] if index is not None and index < len(fragments) else None
        parts = plain(choice)
        if not pieces:
            return parts
        emphasised = lambda style: style if "bold" in style else f"bold {style}".strip()  # noqa: E731
        return parts[:-1] + [(emphasised(style) if hovered else style, text) for style, text in pieces]

    control._get_hover_text = lambda choice: row(choice, plain_hover, True)
    control._get_normal_text = lambda choice: row(choice, plain_normal, False)


def match_header(path: str, on: bool) -> str:
    """A rule, then the file to match as ``folder/name``: the folder it sits in, not the whole path."""
    folder, name = os.path.basename(os.path.dirname(path)), os.path.basename(path)
    shown = (color.paint(folder + "/", "cyan", on) if folder else "") + color.paint(name, "bold", on)
    return f"\n{color.paint(color.rule(), 'dim', on)}\n{shown}"


def open_page(value) -> bool:
    """Open the page of a candidate in the default browser. Anything else (Skip, Search...) is ignored."""
    url = getattr(value, "url", None)
    return bool(url) and webbrowser.open(url)


def follow_resize(prompt, picture) -> None:
    """Draw ``picture`` again when the terminal changes size (the terminal drops it when it re-flows the screen)."""
    app = prompt.application

    def after_render(_app) -> None:
        try:
            size = app.output.get_size()
            if size.columns == picture.terminal_columns:
                return
            picture.terminal_columns = size.columns
            thumbnail.clear(picture.kind, _OutputStream(app.output))
            if size.columns >= thumbnail.MIN_TERMINAL_COLUMNS:
                up = app.renderer._cursor_pos.y  # rows between the question's first line and the cursor
                _OutputStream(app.output).write(thumbnail.redraw(picture, size.columns, up, thumbnail.in_tmux()))
        except Exception as err:  # a picture is a help: never the reason the question breaks
            import logging
            logging.getLogger(__name__).debug(f"no thumbnail redraw: {err}")

    app.after_render += after_render


class _OutputStream:
    """A prompt_toolkit output seen as a stream, so the picture is written in order with the screen updates."""

    def __init__(self, output):
        self.output = output

    def write(self, text: str) -> None:
        self.output.write_raw(text)

    def flush(self) -> None:
        self.output.flush()


def bind_open(prompt, keys: tuple = ("c-o",)) -> None:
    """Make Ctrl-O open the page of the highlighted choice, in a select / fuzzy prompt."""
    def _open(event):
        open_page(prompt.result_value)

    for key in keys:
        prompt.register_kb(key)(_open)


class InquirerPrompter:
    SKIP = "__skip__"
    PREVIOUS = "__previous__"
    ABORT = "__abort__"
    SEARCH = "__search__"

    def __init__(self, source=None, thumbnails: str = "auto"):
        """``source``: the metadata source, when the user may search its album list by hand;
        ``thumbnails``: ``import.thumbnails``."""
        self.source = source
        self.thumbnails = thumbnails

    # -- thin wrappers over InquirerPy, so the logic around them can be tested
    def _select(self, message: str, choices: list, fragments: Optional[list] = None,
                shortcuts: Optional[dict] = None):
        from InquirerPy import inquirer

        prompt = inquirer.select(message=message, choices=choices,
                                 instruction=f"({MATCH_HINT if shortcuts else OPEN_HINT})")
        if shortcuts and getattr(self, "_picture", None) is not None:
            follow_resize(prompt, self._picture)
        bind_open(prompt, ("c-o", "o", "O") if shortcuts else ("c-o",))
        if shortcuts:
            bind_shortcuts(prompt, shortcuts)
        if fragments:
            colorize_choices(prompt, fragments)
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
        on = color.enabled()
        choices, fragments, keys = [], [], {}

        def add(name, value, pieces, key=None):
            choices.append({"name": name, "value": value})
            fragments.append(pieces if on else None)  # no colour: the plain name is drawn
            if key:
                keys[key] = value

        for number, m in enumerate(matches[:9], start=1):
            slug = m.candidate.url.rsplit("/", 1)[-1]
            add(f"[{number}] {m.score:5.1f}  {slug}", m.candidate,
                shortcut(str(number), "", "") + [("", " "), (f"fg:{color.score_hex(m.score)} bold", f"{m.score:5.1f}"),
                                                 ("", f"  {slug}")], str(number))
        if self.source is not None:
            add("[E]nter a search in the album list...", self.SEARCH, shortcut("E", "nter a search in the album list...", "fg:ansicyan"), "e")
        add("[S]kip this file", self.SKIP, shortcut("S", "kip this file", "fg:ansiyellow"), "s")
        add("[B]ack to the previous file", self.PREVIOUS, shortcut("B", "ack to the previous file", "fg:ansiblue"), "b")
        add("[A]bort the import (what is decided is kept; run it again to continue)", self.ABORT,
            shortcut("A", "bort the import (what is decided is kept; run it again to continue)", "fg:ansired"), "a")
        print(match_header(path, on))
        self._thumbnail(path)
        try:
            return self._ask_match(path, choices, fragments, keys)
        finally:
            self._clear_thumbnail()

    def _ask_match(self, path, choices, fragments, keys):
        while True:
            answer = self._select("Which album?", choices, fragments, keys)
            if answer == self.ABORT:
                raise ImportAborted(path)
            if answer == self.PREVIOUS:
                raise GoBack(path)
            if answer == self.SKIP:
                return None
            if answer == self.SEARCH:
                found = self.search(path)
                if found is not None:
                    return found
                continue  # nothing picked: back to the list
            return answer

    def _thumbnail(self, path: str) -> None:
        """A small picture of the file's first page on the right of the question, on a terminal that can show it."""
        kind = thumbnail.protocol(self.thumbnails)
        if kind is None:
            return
        try:
            thumbnail.clear(kind)  # the previous album's picture, which would show through
            self._drawn = kind
            from bdnex.importer import covers

            image = covers.front_cover(path, os.path.splitext(path)[1].lstrip(".").lower())
            if image:
                picture = thumbnail.prepare(image, kind, shutil.get_terminal_size((80, 24)).columns)
                if picture is not None:
                    thumbnail.draw(picture)
                    self._picture = picture
        except Exception as err:  # a picture is a help: never the reason the question can not be asked
            import logging
            logging.getLogger(__name__).debug(f"no thumbnail for {path}: {err}")

    def _clear_thumbnail(self) -> None:
        self._picture = None
        kind, self._drawn = getattr(self, "_drawn", None), None
        if kind:
            try:
                thumbnail.clear(kind)
            except Exception:  # a picture is a help
                pass

    def confirm_series(self, folder: str, paths: list[str], proposal) -> str:
        """Several files in one folder: are they one series? ``series`` or ``each`` (match them one by one)."""
        self._need_terminal()
        shown, on = 25, color.enabled()
        print(f"\n{color.paint(color.rule(), 'dim', on)}\n{color.paint(folder + '/', 'cyan', on)} ({len(paths)} files)")
        for path in paths[:shown]:
            print(f"  {color.paint(os.path.basename(path), 'bold', on)}")
        if len(paths) > shown:
            print(f"  ... and {len(paths) - shown} more")
        print(f"These files look like one series: {color.paint(chr(34) + proposal.name + chr(34), 'cyan_bold', on)} "
              f"({proposal.covered} of {len(paths)} files have their volume in it).")
        answer = self._select("Is this one series?", [
            {"name": f"Yes: match them by volume number in \"{proposal.name}\"", "value": "series"},
            {"name": "No: match each file by itself", "value": "each"},
            {"name": "Abort the import (what is decided is kept; run it again to continue)", "value": self.ABORT}])
        if answer == self.ABORT:
            raise ImportAborted(folder)
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
