"""Search the whole album list by text, as ``fzf`` does in the review screen: no network, no library write.

``search_folded`` is the one search (the review screen and ``bdnex search`` both use it). ``search_file`` adds what a person
types after looking at a file: the series from its folder, the title words and the volume from its name, then ranks the
albums found by every query with the volume asked for.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from bdnex.importer.matching import (query_from_filename, queries_for, rerank, series_queries, volumes_for)
from bdnex.library.query import fold
from bdnex.ui.prompts import display_name

COVERAGE_BONUS = 10.0  # at most, for an album whose name has every word of the file's
POOL = 60  # albums read per query before the volume is looked at
TOO_MANY = 4000  # a letter or two: too many names to rank, the shorter ones are listed


def folded_names(catalogue: list) -> list[str]:
    """What the search compares the text with: each album's readable name, accents and case folded."""
    return [fold(display_name(candidate)) for candidate in catalogue]


def _volume_of(query: str) -> tuple[str, int | None]:
    """``batmandarkvic2`` -> (``batmandarkvic``, 2): a number at the end of what is typed is the volume, not letters of the name."""
    found = re.match(r"^(.*[a-z])\s*0*(\d{1,3})$", query)
    return (found.group(1), int(found.group(2))) if found else (query, None)


def subsequence_score(letters: str, name: str) -> float | None:
    """How well ``letters`` (what was typed, no spaces) are found in order in ``name``, as ``fzf`` finds them: ``None`` when
    they are not, else 0-100 (tight, and starting on a word, is higher). ``batmandarkvic`` finds ``batman dark victory``."""
    squeezed = name.replace(" ", "")
    first = squeezed.find(letters[0])
    best = None
    for _ in range(4):  # a few places where the first letter appears: the tightest run wins
        if first == -1:
            break
        position, ok = first, True
        for letter in letters[1:]:
            position = squeezed.find(letter, position + 1)
            if position == -1:
                ok = False
                break
        if not ok:
            break
        tight = len(letters) / (position - first + 1)
        score = 55 + 35 * tight + (5 if _word_start(name, first) else 0)
        best = score if best is None or score > best else best
        first = squeezed.find(letters[0], first + 1)
    return best if best is not None and best >= 62 else None


def _word_start(name: str, index: int) -> bool:
    """Is the ``index``-th letter of ``name`` (spaces not counted) the first of a word?"""
    seen = 0
    for number, char in enumerate(name):
        if char == " ":
            continue
        if seen == index:
            return number == 0 or name[number - 1] == " "
        seen += 1
    return False


def search_folded(catalogue: list, folded: list[str], text: str, limit: int) -> tuple[list, int]:
    """The albums of the whole list that look like ``text``, best first, as ``([(candidate, score)], matches)``.

    Every word has to be in the name (case and accents do not matter); when too few names have all of them, the letters typed are
    looked for in order, spaces ignored, as ``fzf`` does (``batmandarkvic2``, ``bat dk victory``: a trailing number is the volume); and when that finds
    nothing, a fuzzy match over the whole list (a typo) takes over, with a floor so that unrelated names are not listed."""
    from rapidfuzz import fuzz, process

    query = fold(text).strip()
    if not query:
        return [], len(catalogue)
    words = query.split()
    pool = [i for i, name in enumerate(folded) if all(word in name for word in words)]
    if len(pool) > TOO_MANY:
        pool.sort(key=lambda i: len(folded[i]))
        return [(catalogue[i], 0.0) for i in pool[:limit]], len(pool)
    found: list[tuple[int, float]] = []
    if pool:
        ranked = process.extract(query, {i: folded[i] for i in pool}, scorer=fuzz.WRatio, limit=limit)
        found = [(key, score) for _, score, key in ranked]
    if len(pool) < limit:  # too few names with every word: the letters in order (fzf), the volume split off
        letters, volume = _volume_of(query)
        letters = re.sub(r"[^a-z0-9]", "", letters)
        if len(letters) >= 3:
            have = {key for key, _ in found}
            extra = []
            for index, name in enumerate(folded):
                if index in have:
                    continue
                score = subsequence_score(letters, name)
                if score is not None:
                    bonus = 10 if volume is not None and catalogue[index].volume == volume else (-15 if volume is not None and catalogue[index].volume is not None else 0)
                    extra.append((index, min(100.0, max(0.0, score + bonus))))
            extra.sort(key=lambda item: (-item[1], len(folded[item[0]])))
            found += extra[: max(0, limit - len(found))]
    if not found:  # a typo: a fuzzy match over the whole list
        ranked = process.extract(query, folded, scorer=fuzz.WRatio, limit=limit, score_cutoff=60)
        found = [(index, score) for _, score, index in ranked]
    found.sort(key=lambda item: (-item[1], catalogue[item[0]].volume if catalogue[item[0]].volume is not None else 0, catalogue[item[0]].url))
    return [(catalogue[index], score) for index, score in found], max(len(pool), len(found))


class AlbumList:
    """The album list of a source, loaded once, searched by text."""

    def __init__(self, source):
        self.source = source
        self.catalogue = list(source.catalogue())
        self.folded = folded_names(self.catalogue)

    def search(self, text: str, limit: int = 20) -> tuple[list, int]:
        return search_folded(self.catalogue, self.folded, text, limit)


@dataclass
class Hit:
    candidate: object
    score: float  # 0-100: the best text score, then the volume asked for
    found_by: list[str] = field(default_factory=list)  # the queries that found it
    parts: tuple[str, ...] = ()  # what moved the score after the text score


EDITION_WORDS = {"integrale", "integral", "integrales", "tome", "tomes", "volume", "vol", "deluxe", "digital", "hs", "int", "edition", "collection",
                 "complet", "complete", "luxe", "t"}  # what an edition adds to a series name: a search without them finds the series first


def file_queries(path: str) -> list[str]:
    """What to search for a file, from the most to the least specific: the file name, behind its folder's name, ``series volume``
    from the folder and from the words before the volume, the same without the words an edition adds (``intégrale``, ``tome``,
    ``deluxe``), the first two words with the volume, then each of the last two folders alone (a series is often the folder)."""
    queries = [*queries_for(path), *series_queries(path)]
    parts = [part for part in os.path.dirname(path).split(os.sep) if part][-2:]
    folders = [query_from_filename(part + ".x") for part in reversed(parts)]
    volumes = volumes_for(path)
    for base in [query_from_filename(path), *folders]:
        words = [w for w in base.split() if fold(w) not in EDITION_WORDS and not w.isdigit()]
        if words and len(words) != len(base.split()):
            queries.append(" ".join(words) + (f" {volumes[0]}" if volumes else ""))  # without the edition words
        if len(words) > 2:
            queries.append(" ".join(words[:2]) + (f" {volumes[0]}" if volumes else ""))  # the first two words and the volume
    queries += folders
    seen, kept = set(), []
    for query in queries:
        key = fold(query).strip()
        if len(key) >= 3 and key not in seen:
            seen.add(key)
            kept.append(query)
    return kept


def search_file(albums: AlbumList, path: str, limit: int = 20, extra: tuple[str, ...] = ()) -> list[Hit]:
    """The albums that could be ``path`` among the whole list: every query of :func:`file_queries` (and ``extra``, what the
    caller read in the file), the albums merged by address, the volume the file name asks for rewarded (a wrong one cut)."""
    found: dict[str, Hit] = {}
    for query in [*extra, *file_queries(path)]:
        hits, _ = albums.search(query, limit=max(limit * 5, POOL))  # many albums tie on the words: the volume tells them apart below
        for candidate, score in hits:
            known = found.get(candidate.url)
            if known is None:
                found[candidate.url] = Hit(candidate, score, [query])
            else:
                known.score = max(known.score, score)
                known.found_by.append(query)
    ranked = list(found.values())  # not cut here: the volume (below) decides between the albums that tie on the words
    by_url = {hit.candidate.url: hit for hit in ranked}
    matches = rerank(query_from_filename(path), [(hit.candidate, hit.score) for hit in ranked], volumes_for(path), albums.source,
                     integral=bool(re.search(r"\bint[eé]grales?\b", os.path.basename(path), re.I)) or None)
    words = [w for w in fold(query_from_filename(path)).split() if len(w) >= 3 and not w.isdigit()]
    out = []
    for match in matches:
        hit = by_url[match.candidate.url]
        score, parts = match.score, match.parts
        if words:  # a short query (the folder, `Batman`) ties with the right album: the file's own words decide
            name = fold(display_name(hit.candidate))
            share = sum(word in name for word in words) / len(words)
            score = min(100.0, score + COVERAGE_BONUS * share)
            parts = (*parts, f"{share:.0%} of the file's words are in the name: +{COVERAGE_BONUS * share:.0f}")
        out.append(Hit(hit.candidate, score, hit.found_by, parts))
    out.sort(key=lambda hit: (-hit.score, hit.candidate.volume if hit.candidate.volume is not None else 0, hit.candidate.url))
    return out[:limit]
