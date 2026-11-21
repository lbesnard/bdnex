"""The verdicts of ``bdnex review --auto``: look for a better album by cover, and apply what is decided."""

from __future__ import annotations

import io
import logging
import os
import sys

from bdnex.importer import covers
from bdnex.library.recheck import KEPT, UNCONFIRMED, WRONG, Verdict
from bdnex.ui.review_cmd import settle

logger = logging.getLogger(__name__)

MEANING = {KEPT: "confirmed by something other than the name", WRONG: "something contradicts it",
           UNCONFIRMED: "nothing confirms it"}
OWN_FLAG = "applied at"  # the start of the reason of a flag put on a medium match; one that is not contradicted any more is taken back


def add_candidate_options(parser) -> None:
    parser.add_argument("--candidates", type=int, default=5, metavar="N",
                        help="for each contradicted file, compare the covers of its N best other candidates (the saved "
                             "cover when there is one, else one request each; needs OpenCV) and propose the album whose "
                             "cover is clearly the file's; 0: do not (default 5)")
    parser.add_argument("--no-cover", action="store_true",
                        help="do not compare covers at all (neither the album's, not yet compared, nor the candidates')")


def tint(text: str, on: bool) -> str:
    """The lines ``look_for_better`` says, coloured by what they are (plain when ``on`` is false)."""
    import re

    from bdnex.ui import color

    if not on:
        return text
    head = re.match(r"^(\[\d+/\d+\]) (.*)\n(\s+now: )(.*)$", text, re.S)
    if head:  # [n/N] file name \n now: album (cover x%)
        return (f"{color.role(head.group(1), 'hint', on)} {color.role(head.group(2), 'name', on)}\n"
                f"{head.group(3)}{color.role(head.group(4), 'name', on)}")
    candidate = re.match(r"^(\s+[* ] cover\s+)(\d+)%(.*)$", text)
    if candidate:  # `* cover  85%  Album  (score 80; saved cover)`: the percentage in the colour of the score
        return candidate.group(1) + color.paint_score(f"{candidate.group(2)}%", float(candidate.group(2)), on) \
            + color.role(candidate.group(3), "note", on)
    stripped = text.strip()
    if stripped.startswith("-> better"):
        return color.role(text, "good", on)
    if stripped.startswith(("-> no clear", "candidates not compared", "no other candidate")) or "not compared" in stripped \
            or " cover   ?" in text:
        return color.role(text, "warn", on)
    if stripped.startswith("-> nothing"):
        return color.role(text, "note", on)
    return color.hints(text, on)


def look_for_better(settings, library, verdicts: list[Verdict], limit: int, say=lambda text: None) -> int:
    """Set ``better`` on the contradicted verdicts: another candidate whose cover is clearly the file's (the rule of the
    cover vote of an import). Returns how many were found. Errors are swallowed: it is a help, never a reason to fail."""
    wrong = [v for v in verdicts if v.kind == WRONG]
    if limit <= 0 or not wrong:
        return 0
    from bdnex.ui import color

    if color.enabled(sys.stderr):
        raw = say
        say = lambda text: raw(tint(text, True))  # noqa: E731
    if not covers.opencv_installed():
        say("The covers of the other candidates are not compared: OpenCV is not installed (`uv sync --extra cover`).")
        return 0
    from bdnex.importer.matching import find_matches
    from bdnex.importer.registry import create_source
    from bdnex.importer.session_vote import LEAD
    from bdnex.ui.candidates_cmd import compare_covers

    try:
        source = create_source(settings.source, library, settings)
        if not source.is_ready():
            say("The covers of the other candidates are not compared: the album list is not downloaded (`bdnex init`).")
            return 0
        warm = getattr(source, "warm", None)
        if warm:
            warm()
    except Exception as err:
        say(f"The covers of the other candidates are not compared: {err}")
        return 0
    say(f"Comparing the covers of the {limit} best other candidates of {len(wrong)} contradicted file(s) "
        "(a cover not saved yet is one request; Ctrl-C stops). `*` is the album now linked.")
    unreadable = 0
    medium, vote_min = settings.match.medium_threshold, settings.match.cover_vote_min
    found = 0
    for number, verdict in enumerate(wrong, start=1):
        say(f"[{number}/{len(wrong)}] {os.path.basename(verdict.path)}\n"
            f"    now: {verdict.album}" + ("" if verdict.cover is None else f"  (its cover is {verdict.cover:.0f}% like the file's)"))
        try:
            row = library.get_file(verdict.path) or {}
            matches = find_matches(source, verdict.path, limit=limit)
            entries = [{"for": [], "against": []} for _ in matches]
            compare_covers(source, settings, verdict.path, row.get("format"), matches, entries)
            if matches and all(entry.get("cover") is None for entry in entries):
                unreadable += 1  # the file's first page could not be read (a disk not mounted?) or no cover could be fetched
        except KeyboardInterrupt:
            raise
        except Exception as err:
            logger.debug(f"{verdict.path}: candidates not compared ({err})")
            say(f"    candidates not compared: {err}")
            continue
        if not matches:
            say("    no other candidate in the album list")
        scored = []
        for match, entry in zip(matches, entries):
            known = library.find_album(source.name, match.candidate.source_id)
            album = library.get_album(known) if known is not None else None
            name = f"{album.get('series') or ''} {album['title']}".strip() if album else match.candidate.name
            current = known is not None and known == verdict.album_id
            if entry.get("cover") is None:
                say(f"    {'*' if current else ' '} cover   ?  {name}  (score {match.score:.0f}; {entry.get('cover_note', 'no cover')})")
            else:
                say(f"    {'*' if current else ' '} cover {entry['cover']:>3.0f}%  {name}  (score {match.score:.0f}; "
                    f"{entry.get('cover_from', '?')} cover)")
                scored.append((entry["cover"], match, known, name))
        mine = [item[0] for item in scored if item[2] == verdict.album_id]
        others = [item for item in scored if item[2] != verdict.album_id and item[1].score >= medium]
        if not others:
            say("    -> nothing to switch to")
            continue
        top = max(others, key=lambda item: item[0])
        beaten = max([*(item[0] for item in scored if item[1] is not top[1]), *mine, verdict.cover or 0.0])
        if top[0] >= vote_min and top[0] - beaten >= LEAD:
            verdict.better = {"url": top[1].candidate.url, "album": top[3], "score": round(top[1].score),
                              "cover": top[0], "current_cover": beaten}
            found += 1
            say(f"    -> better: {top[3]} (cover {top[0]:.0f}%, the next best {beaten:.0f}%)")
        else:
            say(f"    -> no clear winner (needs a cover of {vote_min:.0f}% and {LEAD:.0f} points ahead; best {top[0]:.0f}%, "
                f"next {beaten:.0f}%)")
    say(f"Covers compared: {found} better album(s) found"
        + (f"; for {unreadable} file(s) no cover could be compared (is the disk mounted? `-vv` says why)" if unreadable else "")
        + ".")
    return found


def rematch(path: str, url: str, keep_both: bool = False) -> bool:
    """Link the file to the album at ``url`` as ``bdnex import --album`` does (the library only; no file is touched).
    ``keep_both``: also when the album has another file (this one becomes a spare version)."""
    from bdnex.ui import import_cmd

    # --unattended: no question. The review screen owns the terminal, so a question (the cover looks different...) would wait for
    # a key that its own input swallows, for ever. The link is the person's choice; a low cover only shows in the table.
    code = import_cmd.run([path, "--album", url, "--database-only", "--unattended", *(["--keep-both"] if keep_both else [])],
                          out=io.StringIO())
    return code == 0


def better_text(v: Verdict) -> str:
    b = v.better
    return f"{b['album']} (its cover is {b['cover']:.0f}% like the file's, the others' at most {b['current_cover']:.0f}%)"


def settle_verdicts(library, verdicts: list[Verdict], *, yes: bool = False, prompter=None, out=sys.stdout) -> dict[str, int]:
    """Apply the verdicts by rule: the one place (``bdnex review --auto``).

    kept: the flag goes. wrong: switched to the better album when a cover found one, else unlinked (remembered as skipped,
    so ``import --skipped`` decides it again). unconfirmed: left as it is (a flag on a medium match that nothing
    contradicts is taken back: no evidence is no reason to ask you)."""
    count = {"cleared": 0, "switched": 0, "unlinked": 0, "refused": 0}
    reviews = {v.path: (library.get_file(v.path) or {}).get("review") or "" for v in verdicts}
    switch = [v for v in verdicts if v.kind == WRONG and v.better]
    cut = [v for v in verdicts if v.kind == WRONG and not v.better]
    if (switch or cut) and not yes:
        if prompter is None:
            from bdnex.ui.prompts import new_prompter
            prompter = new_prompter()
        what = " and ".join(part for part in (f"switch {len(switch)} file(s) to a better album" if switch else "",
                                              f"unlink {len(cut)} file(s) from their album" if cut else "") if part)
        if not prompter.confirm(f"{what.capitalize()}?"):
            print("Nothing changed.", file=out)
            raise Declined
    for v in verdicts:
        flag = reviews[v.path]
        if flag and (v.kind == KEPT or (v.kind == UNCONFIRMED and flag.startswith(OWN_FLAG))):
            library.clear_review(v.path)
            count["cleared"] += 1
    for v in switch:
        rematch(v.path, v.better["url"])
        now = (library.get_file(v.path) or {}).get("album_id")
        if now is not None and now != v.album_id:  # the library says so, not the import's exit code
            count["switched"] += 1
            continue
        count["refused"] += 1  # e.g. the better album already has another file (a duplicate): nothing was changed
        print(f"  not switched: {v.path}\n    the album {v.better['url']} was refused (it may already have another file: "
              "`bdnex duplicates`); the file stays on its contradicted album, flagged for you", file=out)
        library.flag_review(v.path, f"better: {better_text(v)}; not switched: the album was refused (another file may have it)")
    for v in cut:
        row = library.get_file(v.path)
        settle(library, {"path": v.path, "cover_score": row.get("cover_score") if row else None}, "unlink")
        count["unlinked"] += 1
    return count


class Declined(Exception):
    """The person said no to the one question: nothing was changed."""
