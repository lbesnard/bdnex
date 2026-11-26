"""Change many files at once from one form: what they share, what to set on all of them, a volume each.

The form of the review screen (``ui/review_tui.BulkScreen``) is thin: it shows :func:`shared` (the value every album agrees on, or nothing when
they differ), collects only the fields the person changed, and calls :func:`apply`. The checks are ``bdnex edit``'s own
(``edit_cmd.doc_changes``), so the terminal, the web page and this form never disagree on what a valid value is. Everything is checked
first: when one file is refused, nothing is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from bdnex.library import manual
from bdnex.library.review_batch import CHECKED

SERIES, ONESHOT = "series", "oneshot"
NOT_A_CHANGE = ("series",)  # handled apart: it moves the album to a series, it does not rename the old one


@dataclass
class Target:
    path: str
    album_id: Optional[int] = None
    file_id: Optional[int] = None


@dataclass
class Outcome:
    changed: int = 0  # albums whose fields, volume, series or kind changed
    created: int = 0  # hand-written albums made for files that had none
    marked: int = 0  # files marked reviewed
    problems: list[str] = field(default_factory=list)  # why nothing was written (or what was skipped)

    @property
    def ok(self) -> bool:
        return not self.problems


def shared(library, album_ids: list[int]) -> dict[str, Optional[str]]:
    """For every field of the form: the value all these albums have, ``""`` when they all have none, ``None`` when they differ."""
    forms = [manual.form_of(library, album_id) for album_id in album_ids if album_id is not None]
    if not forms:
        return {}
    out: dict[str, Optional[str]] = {}
    for key in forms[0]:
        values = {form.get(key, "") for form in forms}
        out[key] = values.pop() if len(values) == 1 else None
    out["kind"] = _same({(library.get_album(a) or {}).get("kind") for a in album_ids if a is not None})
    return out


def _same(values: set) -> Optional[str]:
    return values.pop() if len(values) == 1 else None


def _doc(values: dict[str, str]) -> dict[str, Any]:
    """The fields of the form as ``edit_cmd.doc_changes`` reads them (credits as ``{role: [names]}``, genres as a list)."""
    doc: dict[str, Any] = {}
    credits: dict[str, list[str]] = {}
    for key, value in values.items():
        if key in NOT_A_CHANGE:
            continue
        if key in manual.CREDIT_KEYS:
            credits[manual.CREDIT_KEYS[key]] = manual._names(value)
        elif key == "genres":
            doc["genres"] = manual._names(value)
        else:
            doc[key] = value.strip() or None
    if credits:
        doc["credits"] = credits
    return doc


def apply(library, targets: list[Target], values: dict[str, str], kind: Optional[str] = None,
          volumes: Optional[dict[str, Optional[int]]] = None, mark: bool = True, specials: Optional[dict[str, Optional[str]]] = None) -> Outcome:
    """Set ``values`` (only the fields to change; an empty text clears a field) on the albums of ``targets``.

    ``kind``: ``"series"`` (the albums join the series named in ``values["series"]``), ``"oneshot"`` (they leave their series) or ``None``
    (as they are; a ``series`` value still moves them to that series). ``volumes``: ``{path: volume}``; ``specials``: ``{path: "HS 1"}``, what the
    site calls a tome that has no number (a hors-série, an intégrale: `alternate_number`; a file is numbered or special, so the screen gives both). A file without album gets a
    hand-written one when it is asked to become a series album or has a title; otherwise it is reported. ``mark``: the files are
    marked reviewed. Nothing is written when something is refused."""
    from bdnex.ui import edit_cmd

    out = Outcome()
    volumes, specials = volumes or {}, specials or {}
    series = (values.get("series") or "").strip()
    if kind not in (None, SERIES, ONESHOT):
        out.problems.append(f"unknown kind {kind!r}")
        return out
    if kind == SERIES and not series:
        out.problems.append("name the series (the albums join it)")
        return out
    errors: list[str] = []
    doc = _doc(values)
    plan = []  # (target, album or None, changes)
    seen_volume: dict[int, Optional[int]] = {}
    for target in targets:
        album = library.get_album(target.album_id) if target.album_id is not None else None
        if album is None:
            if not (kind == SERIES or (values.get("title") or "").strip()):
                out.problems.append(f"{_name(target)}: no album (link it first, or give it a title or a series)")
                continue
            try:
                manual.check({**{k: v for k, v in values.items() if k not in manual.CREDIT_KEYS or v}, "title": values.get("title", "")},
                             series=kind == SERIES)
            except manual.FormError as err:
                errors.append(f"{_name(target)}: {err}")
                continue
            plan.append((target, None, {}))
            continue
        problems: list[str] = []
        changes = edit_cmd.doc_changes(album, doc, problems) if doc else {}
        if target.path in volumes or target.path in specials:
            volume, special = volumes.get(target.path, album.get("volume")), specials.get(target.path, album.get("alternate_number"))
            if album["id"] in seen_volume and seen_volume[album["id"]] != (volume, special):
                problems.append(f"{_name(target)}: two files of one album, two numbers")
            seen_volume[album["id"]] = (volume, special)
            if volume != album.get("volume"):
                changes["volume"] = volume
            if (special or None) != (album.get("alternate_number") or None):
                changes["alternate_number"] = special or None
        errors.extend(problems)
        plan.append((target, album, changes))
    if errors:
        out.problems.extend(errors)
        return out
    for target, album, changes in plan:
        if album is None:
            volume = volumes.get(target.path)
            manual.create(library, target.path, {**values, "series": series if kind == SERIES else "",
                                                 "alternate_number": specials.get(target.path) or values.get("alternate_number", "")}, volume)
            out.created += 1
            target.album_id = (library.get_file(target.path) or {}).get("album_id")
            _mark(library, target, out, mark)
            continue
        touched = bool(changes)
        if changes:
            library.edit_album(album["id"], changes)
        if kind == ONESHOT and album["kind"] == SERIES:
            library.make_oneshot(album["id"])
            touched = True
        elif (kind == SERIES or series) and kind != ONESHOT and (album["kind"] != SERIES or album.get("series") != series):
            library.assign_series(album["id"], series)
            touched = True
        out.changed += touched
        _mark(library, target, out, mark)
    return out


def _name(target: Target) -> str:
    return target.path.rsplit("/", 1)[-1]


def _mark(library, target: Target, out: Outcome, mark: bool) -> None:
    """The file was looked at and set by hand: it is reviewed, and a doubt about its match is settled."""
    if not mark:
        return
    from bdnex.ui.review_cmd import settle

    file = library.get_file(target.path) or {}
    if file.get("id") is None:
        return
    if file.get("album_id") is not None:
        settle(library, {"path": target.path}, "keep")
    library.set_attr("file", file["id"], CHECKED, "keep" if file.get("album_id") is not None else "leave")
    out.marked += 1
