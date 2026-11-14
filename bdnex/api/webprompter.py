"""Questions on the web page: the terminal prompter with its four low-level prompts replaced.

Every question of :class:`bdnex.ui.prompts.InquirerPrompter` ends in one of ``_select``, ``_fuzzy``, ``_text`` or
``_checkbox`` (that is how its tests script it). Here those four put a question on the job, wait for the page's answer
and give it back, so every question the terminal can ask, with its keys, labels and colours from ``ui/keys.py``, is
asked on the page without a second copy of the logic.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from bdnex.ui import keys as ui_keys, thumbnail
from bdnex.ui.prompts import InquirerPrompter

TONE_OF = {style: name for name, style in ui_keys.TONES.items()}


def _tone(pieces) -> str:
    """The colour name of a choice from the coloured pieces ``ui.keys.build`` made for it (``""``: plain)."""
    for style, _ in pieces or []:
        for known, name in TONE_OF.items():
            if known in style:
                return name
    return ""


class WebPrompter(InquirerPrompter):
    """``ask(question: dict, image=None) -> dict`` shows a question on the page and returns its answer (see ``Job.ask``).
    The picture a terminal draws beside a question (the file's first page, the two covers of a review) is sent too."""

    def __init__(self, ask: Callable[..., dict], source=None, thumbnails: str = "auto"):
        super().__init__(source=source, thumbnails=thumbnails)
        self._to_page = ask
        self._image: Optional[bytes] = None

    def _ask_page(self, question: dict) -> dict:
        return self._to_page(question, image=self._image)

    # nothing is drawn in a terminal here
    @staticmethod
    def _need_terminal() -> None:
        return None

    @staticmethod
    def _tinted() -> bool:
        return True  # the page draws each answer in its colour; only the terminal's text has no codes

    def _thumbnail(self, path: str) -> None:
        """The file's first page, as a small picture for the question."""
        self._image = None
        try:
            from bdnex.importer import covers

            data = covers.front_cover(path, os.path.splitext(path)[1].lstrip(".").lower())
            small = thumbnail.small_png(data, 360) if data else None
            self._image = small[0] if small else None
        except Exception as err:  # a picture is a help, never the reason a question can not be asked
            import logging
            logging.getLogger(__name__).debug(f"no picture for {path}: {err}")

    def _draw_image(self, image: bytes) -> None:
        small = thumbnail.small_png(image, 720)
        self._image = small[0] if small else None

    def _clear_thumbnail(self) -> None:
        self._image = None

    @staticmethod
    def _plain(choice: Any) -> str:
        return str(choice.get("name", "")) if isinstance(choice, dict) else str(getattr(choice, "name", choice))

    @staticmethod
    def _value(choice: Any) -> Any:
        return choice.get("value") if isinstance(choice, dict) else getattr(choice, "value", choice)

    def _listed(self, choices: list, fragments: Optional[list] = None, shortcuts: Optional[dict] = None) -> list[dict]:
        keys: dict[int, str] = {}
        for key, value in (shortcuts or {}).items():
            for index, choice in enumerate(choices):
                if self._value(choice) is value or (index not in keys and self._value(choice) == value):
                    keys[index] = key
                    break
        def page_of(choice) -> str:  # a candidate album has the address of its page on the site
            url = getattr(self._value(choice), "url", None)
            return url if isinstance(url, str) and url.startswith(("https://", "http://")) else ""

        return [{"index": i, "label": self._plain(c), "key": keys.get(i, ""), "url": page_of(c),
                 "tone": _tone(fragments[i]) if fragments and i < len(fragments) else "",
                 "ticked": bool(c.get("enabled")) if isinstance(c, dict) else False} for i, c in enumerate(choices)]

    def _select(self, message: str, choices: list, fragments: Optional[list] = None, shortcuts: Optional[dict] = None):
        answer = self._ask_page({"kind": "select", "message": message, "choices": self._listed(choices, fragments, shortcuts)})
        return self._value(choices[int(answer["choice"])])

    def _fuzzy(self, message: str, choices: list):
        answer = self._ask_page({"kind": "search", "message": message, "choices": self._listed(choices)})
        chosen = answer.get("choice")
        return None if chosen is None else self._value(choices[int(chosen)])

    def _text(self, message: str, default: str = "") -> str:
        return str(self._ask_page({"kind": "text", "message": message, "default": default}).get("text", ""))

    def _checkbox(self, message: str, choices: list):
        answer = self._ask_page({"kind": "checkbox", "message": message, "choices": self._listed(choices)})
        return [self._value(choices[int(i)]) for i in answer.get("choices", [])]
