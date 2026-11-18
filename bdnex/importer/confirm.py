"""A second look at a match, with what the album page says: do the file and the album agree?

The first match only has names (the sitemap address). Once the album page is read there is more to compare: the
credits, the year, the volume, and the file's own tags. :func:`assess` weighs those signals for one file and says
``confirmed``, ``unverified`` (nothing to compare), ``doubtful`` or ``contradicted``, with the reasons in plain words.
It reads nothing but its arguments (no network, no file, no database), so it is cheap to run on a whole library.

Signals are *for* or *against*. A signal against is **strong** when it is hard to explain by anything but a wrong
album (another volume number, the file's own tags naming another series, a "supplement" for a numbered tome) and
**weak** when it has common innocent causes (a reissue has another year, a name in parentheses is often the publisher or
the scanner, not an author).
"""

from __future__ import annotations

import difflib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

CONFIRMED, UNVERIFIED, DOUBTFUL, CONTRADICTED = "confirmed", "unverified", "doubtful", "contradicted"
NEEDS_LOOK = (DOUBTFUL, CONTRADICTED)

# words of a "(group)" in a file name that say who made the scan or what it is, never who wrote the book
NOT_AN_AUTHOR = {"digital", "hybrid", "ebook", "ebooks", "french", "fr", "bdfr", "cbz", "cbr", "pdf", "rip", "club",
                 "empire", "scan", "scans", "tome", "vol", "volume", "the", "les", "des", "une", "pour", "and",
                 "avec", "dans", "collectif", "integrale", "intégrale", "edition", "digitale", "lostnerevarine",
                 "printer", "minus", "alpha", "white", "black", "mobile", "num", "numerique", "numérique"}
# words that make a file a special issue, not a numbered tome of the series
SPECIAL = re.compile(r"\b(suppl[ée]ment|hors[- ]?s[ée]rie|hs|int[ée]grale|sp[ée]cial|coffret|artbook)\b", re.I)
YEAR = re.compile(r"(?<!\d)(19[3-9]\d|20[0-3]\d)(?!\d)")
SIMILAR = 0.6  # below this, two titles are not the same book


@dataclass
class Evidence:
    verdict: str = UNVERIFIED
    agree: list[str] = field(default_factory=list)
    strong: list[str] = field(default_factory=list)  # against, hard to explain innocently
    weak: list[str] = field(default_factory=list)  # against, often innocent

    @property
    def reason(self) -> str:
        """One line for ``bdnex review`` and the import report."""
        return "; ".join(self.strong + self.weak)

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "for": self.agree, "against": self.strong + self.weak, "strong": self.strong}


def fold(text: Optional[str]) -> str:
    """Lower case, no accents, words separated by one space."""
    decomposed = unicodedata.normalize("NFD", (text or "").lower())
    return re.sub(r"[\W_]+", " ", "".join(c for c in decomposed if not unicodedata.combining(c))).strip()


def _similar(left: Optional[str], right: Optional[str]) -> float:
    """How alike two titles are: the better of the text ratio and the share of the shorter one found in the other
    (``Demon Slayer`` is inside ``Demon Slayer - Kimetsu no yaiba``)."""
    left, right = fold(left), fold(right)
    if not left or not right:
        return 0.0
    a, b = set(left.split()), set(right.split())
    return max(difflib.SequenceMatcher(None, left, right).ratio(), len(a & b) / min(len(a), len(b)))


def _title_in_file(path: str, album: dict) -> bool:
    """The album's own title (two words or more) is written in the file name: the match rests on it, so a different
    volume number is more likely another numbering than another album."""
    words = fold(album.get("title")).split()
    stem = set(fold(os.path.splitext(os.path.basename(path))[0]).split())
    return len(words) >= 2 and sum(w in stem for w in words) / len(words) >= 0.8


def _year_signal(stem: str, embedded: dict, album: dict, ev: Evidence) -> None:
    wanted = album.get("year")
    if not wanted:
        return
    years = {int(y) for y in YEAR.findall(stem)}
    if str(embedded.get("Year", "")).isdigit() and int(embedded["Year"]) >= 1950:
        years.add(int(embedded["Year"]))
    if not years:
        return
    if any(abs(y - wanted) <= 1 for y in years):
        ev.agree.append(f"the year {wanted}")
    else:
        ev.weak.append(f"the file says {min(years)}, the album is from {wanted} (a reissue, or another album)")


def _author_signal(stem: str, album: dict, ev: Evidence) -> None:
    known = {w for names in (album.get("credits") or {}).values() for name in names for w in fold(name).split()}
    if not known:
        return
    noise = {w for w in fold(album.get("publisher")).split()} | {w for w in fold(album.get("series")).split()}
    words = {w for group in re.findall(r"[(\[]([^()\[\]]*)[)\]]", stem) for w in fold(group).split()
             if len(w) > 2 and not w.isdigit() and w not in NOT_AN_AUTHOR and w not in noise}
    if not words:
        return
    shared = words & known
    if shared:
        ev.agree.append("an author (" + ", ".join(sorted(shared)[:2]) + ")")
    else:
        ev.weak.append("the names in the file name (" + ", ".join(sorted(words)[:3]) + ") are not among the credits")


def _volume_signal(path: str, album: dict, ev: Evidence) -> None:
    from bdnex.importer.matching import query_from_filename, volumes_for

    stem = os.path.splitext(os.path.basename(path))[0]
    volume, special = album.get("volume"), SPECIAL.search(stem)
    title_words = fold(f"{album.get('series') or ''} {album.get('title') or ''}")
    against = ev.weak if _title_in_file(path, album) else ev.strong  # the title agrees: the numbering differs
    if special and volume is not None and not SPECIAL.search(title_words) and not re.search(r"\d", re.sub(SPECIAL, "", stem)):
        against.append(f"the file is a \"{special.group(0).lower()}\" but the album is tome {volume}")
        return
    wanted = volumes_for(path)
    if volume is None or not wanted or not query_from_filename(path):
        return
    if volume in wanted:
        ev.agree.append(f"tome {volume}")
    else:
        against.append(f"the file says tome {', '.join(map(str, wanted[:2]))}, the album is tome {volume}"
                       + (" (same title: perhaps another numbering)" if against is ev.weak else ""))


def _embedded_signal(embedded: dict, album: dict, ev: Evidence) -> None:
    named = [value for value in (embedded.get("Series"), embedded.get("Title")) if value]
    if not named:
        return
    mine = [album.get("series"), album.get("title")]
    best = max(_similar(x, y) for x in named for y in mine)
    if best >= SIMILAR:
        ev.agree.append("its own tags")
    else:
        ev.strong.append(f"the file's own tags say \"{named[0]}\", the album is \"{album.get('series') or album.get('title')}\"")


def assess(path: str, album: dict[str, Any], embedded: Optional[dict] = None) -> Evidence:
    """Weigh what the album says against the file's name and its own tags (``embedded``, the ComicInfo fields or ``None``)."""
    ev = Evidence()
    embedded = embedded or {}
    stem = os.path.splitext(os.path.basename(path))[0]
    _volume_signal(path, album, ev)
    _embedded_signal(embedded, album, ev)
    _year_signal(stem, embedded, album, ev)
    _author_signal(stem, album, ev)
    # the volume number is what the match was ranked on: it agrees by construction, so it confirms nothing
    independent = [a for a in ev.agree if not a.startswith("tome")]
    if ev.strong:
        ev.verdict = CONTRADICTED if not independent else DOUBTFUL
    elif len(ev.weak) >= 2 and not independent:
        ev.verdict = DOUBTFUL
    elif independent:
        ev.verdict = CONFIRMED
    return ev


def worth_a_look(ev: Evidence) -> bool:
    return ev.verdict in NEEDS_LOOK
