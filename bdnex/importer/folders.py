"""Files that sit in the same folder: are they the volumes of a series, versions of one album, or unrelated?

Beets treats a folder as one album and matches its tracks together. Here a folder with several comics
is, most of the time, a series whose files are volumes. :func:`classify` reads the file names to tell
the cases apart, and :func:`propose_series` finds the series the files belong to by letting every file
vote, then maps each file to the album of that series with its volume number.
"""

from __future__ import annotations

import collections
import math
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from bdnex.importer.matching import Match, numeric_title, query_from_filename, small_number
from bdnex.importer.source import Candidate
from bdnex.library.query import fold

SERIES, VERSIONS, UNCLEAR = "series", "versions", "unclear"
MIN_SHARED_LETTERS = 4  # the files of a series start with the same title: at least this many letters
SAME_BOOK_OVERLAP = 0.6  # share of words two file names have in common for them to be one book
MIN_SUPPORT = 0.5  # share of the files with a volume that must agree on a series


def title_and_volume(path: str) -> tuple[list[str], Optional[int]]:
    """The words of a file name (accents and case folded) and its volume number, if it has one.

    A number that opens the name and is followed by another one belongs to the title
    (``100 Bullets - 01`` is volume 1), and numbers of more than three digits are years. A title made of numbers
    keeps them as words (``14-18 - T01``: the words are ``14 18``, the volume 1).
    """
    tokens = fold(query_from_filename(path)).split()
    numbers = [(position, small_number(token)) for position, token in enumerate(tokens) if small_number(token) is not None]
    title, volume = numeric_title(tokens)
    title_numbers = len(title)
    if not title_numbers:
        volume = None
        if numbers:
            volume = numbers[1][1] if numbers[0][0] == 0 and len(numbers) > 1 else numbers[0][1]
    words = [token for position, token in enumerate(tokens)
             if position < title_numbers or (not token.isdigit() and small_number(token) is None)]
    return words, volume


def _shared_prefix(lists: list[list[str]]) -> list[str]:
    prefix = []
    for words in zip(*lists):
        if len(set(words)) != 1:
            break
        prefix.append(words[0])
    return prefix


def classify(paths: list[str]) -> Optional[str]:
    """``series`` (every file has its own volume number and they start alike), ``versions`` (no volume
    or the same one everywhere, and nearly the same name: one album in several files), ``unclear``
    (anything else), ``None`` for a lone file."""
    if len(paths) < 2:
        return None
    parsed = [title_and_volume(path) for path in paths]
    words, volumes = [w for w, _ in parsed], [v for _, v in parsed]
    if all(v is not None for v in volumes) and len(set(volumes)) == len(volumes):
        if len("".join(_shared_prefix(words))) >= MIN_SHARED_LETTERS:
            return SERIES
        return UNCLEAR
    if len(set(volumes)) == 1:  # no volume at all, or the same one in every file: one book, several files
        sets = [set(w) for w in words]
        overlaps = [len(a & b) / max(1, len(a | b)) for i, a in enumerate(sets) for b in sets[i + 1:]]
        if overlaps and min(overlaps) >= SAME_BOOK_OVERLAP:
            return VERSIONS
    return UNCLEAR


@dataclass
class SeriesProposal:
    name: str  # the series, as the source names it
    key: str  # its identity at the source
    mapping: dict[str, Candidate] = field(default_factory=dict)  # file -> the album of the series for its volume
    with_volume: int = 0  # files that have a volume number
    total: int = 0  # files in the folder

    @property
    def covered(self) -> int:
        return len(self.mapping)

    @property
    def solid(self) -> bool:
        """Most of the files found their album in the series."""
        return self.covered >= math.ceil(0.6 * self.total)


def _series_of(source, candidate: Candidate):
    info = source.partial_info(candidate)
    return info.series if info is not None else None


def propose_series(source, paths: list[str], matches: dict[str, list[Match]]) -> Optional[SeriesProposal]:
    """The series most of the files belong to, with the album of each file inside it.

    Every file with a volume number votes for the series whose candidates (among its own matches) have
    that volume; the series with most votes wins if at least half of the files agree. Files are then
    mapped by volume, asking the source again for ``<series> <volume>`` when their own matches lack the
    right album. Files with no volume, or no such album, are left out of the mapping.
    """
    volumes = {path: title_and_volume(path)[1] for path in paths}
    numbered = [path for path in paths if volumes[path] is not None]
    if len(numbered) < 2:
        return None
    votes: collections.Counter = collections.Counter()
    strength: collections.Counter = collections.Counter()
    names: dict[str, str] = {}
    for path in numbered:
        seen = set()
        for match in matches.get(path, []):
            series = _series_of(source, match.candidate)
            if series is None or match.candidate.volume != volumes[path] or series.source_id in seen:
                continue
            seen.add(series.source_id)
            votes[series.source_id] += 1
            strength[series.source_id] += match.score
            names[series.source_id] = series.name
    if not votes:
        return None
    key = max(votes, key=lambda k: (votes[k], strength[k]))
    if votes[key] < max(2, math.ceil(MIN_SUPPORT * len(numbered))):
        return None
    if not _plausible(names[key], paths):
        return None  # the votes agree, but the series has nothing in common with the names: do not suggest it
    proposal = SeriesProposal(names[key], key, with_volume=len(numbered), total=len(paths))
    for path in numbered:
        found = _album_in_series(source, key, names[key], volumes[path], matches.get(path, []))
        if found is not None:
            proposal.mapping[path] = found
    return proposal


def _plausible(series_name: str, paths: list[str]) -> bool:
    """A series must share a word of three letters or more with the folder name or the file names
    (``14-18`` is not ``X``, ``666`` is not ``N6``)."""
    def words(text: str) -> set[str]:
        return {word for word in fold(text).replace("-", " ").replace(".", " ").split() if len(word) >= 3 and word.isalpha()}

    known = words(os.path.basename(os.path.dirname(paths[0])))
    for path in paths:
        known |= set(title_and_volume(path)[0])
    if words(series_name) & known:
        return True
    # a series titled with numbers (14-18, 666) shares no word: its numbers must be the ones the files open with
    numbers = [t for t in fold(series_name).replace("-", " ").replace(".", " ").split() if t.isdigit()]
    if numbers and len(numbers) == len(fold(series_name).replace("-", " ").replace(".", " ").split()):
        return all(title_and_volume(path)[0][:len(numbers)] == numbers for path in paths)
    return False


def _album_in_series(source, key: str, name: str, volume: int, own: list[Match]) -> Optional[Candidate]:
    pool = [(m.candidate, m.score) for m in own] + list(source.candidates(f"{name} {volume}", limit=10))
    best: Optional[tuple[Candidate, float]] = None
    for candidate, score in pool:
        series = _series_of(source, candidate)
        if series is not None and series.source_id == key and candidate.volume == volume:
            if best is None or score > best[1]:
                best = (candidate, score)
    return best[0] if best else None


def propose_for_series(source, key: str, name: str, paths: list[str],
                       matches: dict[str, list[Match]]) -> Optional[SeriesProposal]:
    """The album of one given series for each file, by volume number: what to offer for the rest of a folder once the
    user has picked an album of that series for one of its files. ``None`` when no file finds its volume."""
    volumes = {path: title_and_volume(path)[1] for path in paths}
    numbered = [path for path in paths if volumes[path] is not None]
    proposal = SeriesProposal(name, key, with_volume=len(numbered), total=len(paths))
    for path in numbered:
        found = _album_in_series(source, key, name, volumes[path], matches.get(path, []))
        if found is not None:
            proposal.mapping[path] = found
    return proposal if proposal.mapping else None


def folder_name(paths: list[str]) -> str:
    return os.path.basename(os.path.dirname(paths[0]))


def name_shape(path: str) -> str:
    """The way a file is named, volume number and title left out: ``Tome 01 - Foo.pdf`` -> ``tome``,
    ``Les_Impetueuses_Tribulations_T01.cbz`` -> ``les impetueuses``. Files of one series are usually named alike."""
    stem = os.path.splitext(os.path.basename(path))[0]
    prefix = []
    for word in re.split(r"[\s_.\-]+", fold(stem)):
        if not word:
            continue
        if any(c.isdigit() for c in word):
            break  # the volume number: what comes before it is how the series is named
        prefix.append(word)
    return " ".join(prefix) or fold(stem).split(" ")[0]


def likely_members(proposal, paths: list[str]) -> set[str]:
    """The files to tick at first when the user picks the members of a series: the ones the series was found in,
    named like most of them (a sub-series in the same folder is usually named differently)."""
    mapped = [path for path in paths if path in proposal.mapping]
    if not mapped:
        return set()
    common = collections.Counter(name_shape(path) for path in mapped).most_common(1)[0][0]
    return {path for path in mapped if name_shape(path) == common}
