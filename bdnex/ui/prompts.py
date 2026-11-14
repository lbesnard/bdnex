"""Interactive prompts (InquirerPy) shared by the commands."""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import shlex
import shutil
import subprocess
import sys
import webbrowser
from typing import Optional

from bdnex.importer.matching import Match, query_from_filename
from bdnex.importer.session import GoBack, ImportAborted, NeedsTerminal, SkipFolder
from bdnex.importer.source import Candidate
from bdnex.library.query import fold
from bdnex.ui import color, keys as ui_keys, thumbnail


def display_name(candidate: Candidate) -> str:
    """A readable title for a candidate: the words of its page address, or its normalised name."""
    slug = candidate.url.rsplit("/", 1)[-1]
    if slug.startswith("BD-") and slug.endswith(".html"):
        return re.sub(r"-\d+$", "", slug[3:-5]).replace("-", " ")
    return candidate.name


FUZZY_POOL = 2000  # albums offered to the Python fuzzy prompt: its engine is too slow for the whole list
OPEN_HINT = "Ctrl-O opens the highlighted album's page in your browser"
MATCH_HINT = "Enter or a highlighted key chooses; O opens the highlighted album's page"
PLAIN_HINT = "Enter or a highlighted key chooses"
CONFIRM_HINT = "Enter or a highlighted key chooses; O opens the album's page to check it"
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


_FACTORY: contextvars.ContextVar = contextvars.ContextVar("bdnex_prompter_factory", default=None)


@contextlib.contextmanager
def use_prompter(factory):
    """Inside this block (and this thread) ``new_prompter(...)`` makes ``factory(...)``: the web page asks its
    questions on the page instead of the terminal."""
    token = _FACTORY.set(factory)
    try:
        yield
    finally:
        _FACTORY.reset(token)


def can_ask() -> bool:
    """Is there somebody to ask: a terminal, or the web page (a job with its own prompter)?"""
    return _FACTORY.get() is not None or (sys.stdin.isatty() and sys.stderr.isatty())


def new_prompter(**kwargs) -> "InquirerPrompter":
    """The prompter a command asks its questions with: the terminal's, unless :func:`use_prompter` says otherwise."""
    factory = _FACTORY.get()
    return factory(**kwargs) if factory is not None else InquirerPrompter(**kwargs)


class InquirerPrompter:
    SKIP = "__skip__"
    PREVIOUS = "__previous__"
    ABORT = "__abort__"
    SKIP_FOLDER = "__skip_folder__"
    SEARCH = "__search__"
    URL = "__url__"
    NO = "__no__"

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
                                 instruction=f"({self._hint(shortcuts)})")
        if shortcuts and getattr(self, "_picture", None) is not None:
            follow_resize(prompt, self._picture)
        bind_open(prompt, ("c-o", "o", "O") if shortcuts else ("c-o",))
        if shortcuts:
            bind_shortcuts(prompt, shortcuts)
        if fragments and any(fragments):
            colorize_choices(prompt, fragments)
        return prompt.execute()

    def _pick(self, message: str, entries: list):
        """Ask a question made of :data:`bdnex.ui.keys.ACTIONS` and answers of its own: keys, colours, Enter."""
        choices, fragments, keys = ui_keys.build(entries, self._tinted(), shortcut)
        return self._select(message, choices, fragments, keys)

    @staticmethod
    def _tinted() -> bool:
        """Are the answers coloured (the terminal supports it)? The web page always has their colours."""
        return color.enabled()

    @staticmethod
    def _hint(shortcuts: Optional[dict]) -> str:
        """The line under the question: what the keys do."""
        if not shortcuts:
            return OPEN_HINT
        if "a" not in shortcuts:
            return CONFIRM_HINT
        return MATCH_HINT if "e" in shortcuts or "1" in shortcuts else PLAIN_HINT

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
        offered = []
        if self.source is not None:
            offered.append({"action": "search", "value": self.SEARCH})
        if hasattr(self.source, "candidate_from_url"):
            offered.append({"action": "url", "value": self.URL, "label": "Type the album's URL..."})
        offered += [{"action": "skip", "value": self.SKIP},
                    {"action": "skip_folder", "value": self.SKIP_FOLDER},
                    {"action": "back", "value": self.PREVIOUS},
                    {"action": "abort", "value": self.ABORT,
                     "label": "Abort the import (what is decided is kept; run it again to continue)"}]
        more, more_fragments, more_keys = ui_keys.build(offered, self._tinted(), shortcut)
        choices += more
        fragments += more_fragments
        keys.update(more_keys)
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
            if answer == self.SKIP_FOLDER:
                raise SkipFolder(path)
            if answer == self.SEARCH:
                found = self.search(path)
                if found is not None and self.confirm_found(found):
                    return found
                continue  # nothing picked, or not the right one: back to the list
            if answer == self.URL:
                found = self.ask_url()
                if found is not None:
                    return found
                continue
            return answer

    def confirm_found(self, candidate: Candidate) -> bool:
        """The album picked in the search: check it (O opens its page) before it is used."""
        answer = self._pick(f"Use {display_name(candidate)}?", [
            {"action": "yes", "value": candidate, "label": "Yes, use this album"},
            {"action": "no", "value": self.NO, "label": "No, back to the list"}])
        return answer != self.NO

    def ask_url(self) -> Optional[Candidate]:
        """Type the address of the album's page; empty goes back."""
        while True:
            text = self._text("Address of the album's page (empty to go back):").strip()
            if not text:
                return None
            candidate = self.source.candidate_from_url(text)
            if candidate is not None:
                return candidate
            print("That is not the address of an album page (https://www.bedetheque.com/BD-...-<number>.html).")

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

    def _draw_image(self, image: bytes) -> None:
        """Draw an image (bytes) on the right of the question, as for the match question."""
        kind = thumbnail.protocol(self.thumbnails)
        if kind is None:
            return
        try:
            thumbnail.clear(kind)
            self._drawn = kind
            picture = thumbnail.prepare(image, kind, shutil.get_terminal_size((80, 24)).columns)
            if picture is not None:
                thumbnail.draw(picture)
                self._picture = picture
        except Exception as err:  # a picture is a help, never a reason for the question to fail
            import logging
            logging.getLogger(__name__).debug(f"no picture: {err}")

    def review_file(self, position: str, path: str, album: str, reason: str,
                    file_cover: Optional[bytes], album_cover: Optional[bytes]) -> str:
        """A match whose cover looked different: ``keep`` | ``unlink`` | ``rematch`` | ``skip``.
        The two covers are drawn side by side (the file's on the left) when the terminal can show pictures."""
        self._need_terminal()
        on = color.enabled()
        print(f"\n{color.paint(color.rule(), 'dim', on)}")
        print(f"{color.paint(position, 'cyan', on)}  {color.paint(os.path.basename(os.path.dirname(path)) + '/' + os.path.basename(path), 'bold', on)}")
        print(f"  matched as   {color.paint(album, 'cyan_bold', on)}")
        print(f"  why flagged  {color.paint(reason, 'yellow', on)}")
        if file_cover and album_cover:
            print(f"  {color.paint('left: the file', 'dim', on)}   {color.paint('right: the album on the site', 'dim', on)}")
            both = thumbnail.pair(file_cover, album_cover)
            if both:
                self._draw_image(both)
        elif not album_cover:
            print(f"  {color.paint('the album cover is not downloaded yet: `bdnex import --fetch` gets it', 'dim', on)}")
        try:
            answer = self._pick("What about this file?", [
                {"action": "keep", "value": "keep"},
                {"action": "unlink", "value": "unlink"},
                {"action": "rematch", "value": "rematch"},
                {"action": "skip", "value": "skip", "label": "Skip: leave it flagged for later"},
                {"action": "abort", "value": self.ABORT, "label": "Abort the review (what is decided is kept)"}])
        finally:
            self._clear_thumbnail()
        if answer == self.ABORT:
            raise ImportAborted(path)
        return answer

    def _clear_thumbnail(self) -> None:
        self._picture = None
        kind, self._drawn = getattr(self, "_drawn", None), None
        if kind:
            try:
                thumbnail.clear(kind)
            except Exception:  # a picture is a help
                pass

    def _list_folder(self, folder: str, paths: list[str]) -> None:
        shown, on = 25, color.enabled()
        print(f"\n{color.paint(color.rule(), 'dim', on)}\n{color.paint(folder + '/', 'cyan', on)} ({len(paths)} files)")
        for path in paths[:shown]:
            print(f"  {color.paint(os.path.basename(path), 'bold', on)}")
        if len(paths) > shown:
            print(f"  ... and {len(paths) - shown} more")

    def _series_choice(self, folder: str, proposal) -> str:
        """The keys of the series question: ``series``, ``each``, ``choose`` or ``skip`` (Abort raises)."""
        answer = self._pick("Is this one series?", [
            {"action": "yes", "value": "series", "label": f"Yes: match them by volume number in \"{proposal.name}\""},
            {"action": "no", "value": "each", "label": "No: match each file by itself"},
            {"action": "choose", "value": "choose",
             "label": "Choose which files belong to it (the others are looked at as another series)"},
            {"action": "skip", "value": "skip", "label": "Skip this folder (none of its files is imported now)"},
            {"action": "abort", "value": self.ABORT,
             "label": "Abort the import (what is decided is kept; run it again to continue)"}])
        if answer == self.ABORT:
            raise ImportAborted(folder)
        return answer

    def confirm_series(self, folder: str, paths: list[str], proposal) -> str:
        """Several files in one folder: are they one series? ``series`` or ``each`` (match them one by one)."""
        self._need_terminal()
        on = color.enabled()
        self._list_folder(folder, paths)
        print(f"These files look like one series: {color.paint(chr(34) + proposal.name + chr(34), 'cyan_bold', on)} "
              f"({proposal.covered} of {len(paths)} files have their volume in it).")
        return self._series_choice(folder, proposal)

    def confirm_same_series(self, folder: str, paths: list[str], proposal, first: str) -> str:
        """You picked an album of a series for a file: do the other files of the folder belong to it?"""
        self._need_terminal()
        on = color.enabled()
        self._list_folder(folder, paths)
        print(f"You matched {color.paint(first, 'bold', on)} to the series {color.paint(chr(34) + proposal.name + chr(34), 'cyan_bold', on)}. "
              f"The other files look like more of its volumes ({proposal.covered} of {len(paths)} have theirs in it).")
        return self._series_choice(folder, proposal)

    def _checkbox(self, message: str, choices: list):
        from InquirerPy import inquirer

        return inquirer.checkbox(message=message, choices=choices, instruction="(Space ticks, Enter confirms)",
                                 transformer=lambda picked: f"{len(picked)} file(s) ticked",
                                 raise_keyboard_interrupt=False, mandatory=False).execute()

    def pick_files(self, folder: str, paths: list[str], ticked: set) -> list[str]:
        """Tick the files of a folder that belong to one series. The ones the series was found in start ticked."""
        self._need_terminal()
        choices = [{"name": os.path.basename(path), "value": path, "enabled": path in ticked} for path in paths]
        picked = self._checkbox(f"Which of these {len(paths)} files belong to the series?", choices)
        return list(picked or [])

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
        self._need_terminal()
        return self._pick(f"{what}: {path}\n  already: {', '.join(others)}", [
            {"action": "skip", "value": "skip", "label": "Skip"},
            {"action": "keep_both", "value": "keep_both"},
            {"action": "replace", "value": "replace", "label": "Replace (the new file becomes the album's file)"}])

    def resolve_duplicate(self, comparison, what: str, root=None) -> str:
        """Which file(s) to keep when an album already has one: ``skip`` | ``replace`` | ``keep_both``."""

        from bdnex.versions import KEEP_EXISTING, table

        self._need_terminal()
        intro = ("An identical file is already in the library." if what == "identical file"
                 else "This album already has a file in the library.")
        print(f"\n{comparison.new.name}\n{intro}\n{table(comparison, root)}")
        return self._which_to_keep(comparison, KEEP_EXISTING, "the new one is not imported", "the new one becomes a spare version")

    def _which_to_keep(self, comparison, keep_existing, existing_note: str, both_note: str) -> str:
        """The existing file, the new one or both (``skip`` | ``replace`` | ``keep_both``); the suggested one is first."""
        entries = [{"action": "keep_existing", "value": "skip", "label": f"Keep the existing file ({existing_note})"},
                   {"action": "keep_new", "value": "replace", "label": "Keep the new file (the existing one goes to the trash)"},
                   {"action": "keep_both", "value": "keep_both", "label": f"Keep both ({both_note})"}]
        suggested = "skip" if comparison.suggestion == keep_existing else "replace"
        entries.sort(key=lambda entry: entry["value"] != suggested)
        entries[0]["label"] += "  <- suggested"
        return self._ask("Which one do you keep?", entries)

    def resolve_collision(self, comparison, root=None) -> str:
        """The destination name is taken: ``skip`` | ``replace`` | ``keep_both`` (as a spare version)."""
        from bdnex.versions import KEEP_EXISTING, table

        self._need_terminal()
        print(f"\n{comparison.new.name}\nThe destination is already taken by another file.\n{table(comparison, root)}")
        return self._which_to_keep(comparison, KEEP_EXISTING, "the new one is left where it is", "the new one goes to the spare versions")

    def cover_mismatch(self, path: str, album: str, score: float, threshold: float) -> str:
        """The file's cover does not look like the album's: ``keep`` the match or ``unlink`` it."""
        self._need_terminal()
        return self._pick(
            f"The cover of {os.path.basename(path)} looks little like the cover of {album} "
            f"({score:.0f}% < {threshold:.0f}%).",
            [{"action": "keep", "value": "keep", "label": "Keep this match"},
             {"action": "unlink", "value": "unlink",
              "label": "Unlink it (it will be looked at again at the next import --force)"}])

    def choose_placement(self, count: int, directory: str) -> str:
        """What to do with files imported from outside the library folder: ``move`` | ``copy`` | ``leave``."""
        return self._pick(f"{count} imported file(s) are outside your collection ({directory}). What now?", [
            {"action": "move", "value": "move",
             "label": "Move them into the library (tags are written, names follow your templates)"},
            {"action": "copy", "value": "copy", "label": "Copy them into the library (the originals stay where they are)"},
            {"action": "leave", "value": "leave",
             "label": "Leave them where they are (`bdnex move staged:true` places them later)"}])

    # -- write / move
    def _ask(self, message: str, entries: list):
        """A question of the write / move run: its answers, then **[A]bort the run** (key ``a``).

        ``entries`` are :func:`bdnex.ui.keys.build` entries: every answer has the key and colour of its action.
        """
        from bdnex.organizer.organizer import WriteAborted

        abort = {"action": "abort", "value": self.ABORT, "label": "Abort the run (what is done stays; run it again to continue)"}
        answer = self._pick(message, [*entries, abort])
        if answer == self.ABORT:
            raise WriteAborted()
        return answer

    def conflict(self, path: str, diff_lines: list[str]) -> str:
        from bdnex.ui import diffview
        from bdnex.ui.color import enabled

        print("\n" + diffview.conflict_text(path, diff_lines, enabled()))
        return self._ask("Which one wins?", [
            {"action": "database", "value": "database"},
            {"action": "database_all", "value": "database_all", "label": "Database wins, here and for the rest of the queue"},
            {"action": "file", "value": "file"},
            {"action": "choose", "value": "fields", "label": "Choose field by field (or type your own value)"},
            {"action": "skip", "value": "skip"}])

    def resolve_fields(self, path: str, changes: list, file_fields: dict, db_fields: dict) -> Optional[dict]:
        """For each difference: the database's value, the file's, or one typed by hand (default: the database's)."""
        from bdnex.organizer.comicinfo import excerpts

        chosen = {}
        for number, change in enumerate(changes, start=1):
            mine, theirs = db_fields.get(change.field), file_fields.get(change.field)
            short_db, short_file, _ = excerpts(mine, theirs)
            choices = [{"action": "database", "value": "db", "label": f"Database: {short_db}", "key": "d"}]
            if theirs is not None:
                choices.append({"action": "file", "value": "file", "label": f"File:     {short_file}", "key": "f"})
            choices.append({"label": "Type a value...", "value": "edit", "key": "t", "tone": "cyan"})
            choices.append({"action": "skip", "value": "skip"})
            answer = self._ask(f"[{number}/{len(changes)}] {change.field}", choices)
            if answer == "skip":
                return None
            if answer == "edit":
                chosen[change.field] = self._text(f"{change.field}:", mine or theirs or "")
            else:
                chosen[change.field] = mine if answer == "db" else theirs
        return chosen

    def confirm(self, message: str, default: bool = False) -> bool:
        choices = [{"action": "yes", "value": "yes"}, {"action": "no", "value": "no"}]
        choices.sort(key=lambda choice: choice["value"] != ("yes" if default else "no"))
        return self._ask(message, choices) == "yes"

    def ask_value(self, field: str, label: str) -> Optional[str]:
        value = self._text(f"`{field}` is empty for \"{label}\". Value (empty = skip the file):")
        return value.strip() or None

    def collision(self, dest: str) -> str:
        return self._ask(f"Destination exists: {dest}", [
            {"action": "skip", "value": "skip", "label": "Skip"},
            {"action": "keep_both", "value": "keep_both", "label": "Keep both (add a number)"},
            {"action": "replace", "value": "replace"}])

    def size_action(self, path: str, before: int, after: int, tolerance: float, drawn: int = 0) -> str:
        from bdnex.formatting import human_size

        print(f"\n{path}\nThe new archive is {human_size(after)}, the original {human_size(before)} "
              f"(more than {tolerance:g}% apart). "
              + (f"{drawn} page(s) had to be drawn from the PDF: they are new pictures, not copies of its own."
                 if drawn else "Every page is in it, byte for byte."))
        return self._ask("What now?", [
            {"action": "refuse", "value": "refuse"},
            {"action": "accept", "value": "accept"}])

    def corrupted_action(self, path: str, problems: list[str], can_move: bool) -> str:
        print(f"\n{path}\nThe archive is damaged and could not be repaired:")
        for line in problems:
            print(f"  - {line}")
        choices = [{"action": "leave", "value": "leave"}]
        if can_move:
            choices.append({"action": "move", "value": "move", "label": "Move it anyway (as it is, no tags written)"})
        return self._ask("What now?", choices)
