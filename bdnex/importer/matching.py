"""Score filename -> album candidates and turn them into a recommendation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from bdnex.importer.source import Candidate

VOLUME_MATCH_BONUS = 5.0
VOLUME_MISMATCH_PENALTY = 15.0
SERIES_QUERY_CAP = 89.0  # matched on series + volume only: always ask, never apply silently
NO_VOLUME_CAP = 85.0  # the file has a volume but the album has none (e.g. an album named like the series)
VOLUME_MISMATCH_CAP = 85.0  # a wrong volume is never a strong match, whatever the title says


class Recommendation(Enum):
    STRONG = "strong"  # apply without asking
    MEDIUM = "medium"  # ask the user
    NONE = "none"  # no usable match


@dataclass(frozen=True)
class Match:
    candidate: Candidate
    score: float  # 0-100, after the volume adjustment
    base_score: float  # plain title similarity


VOLUME_GROUP = re.compile(r"^\s*(?:t|tome|vol\.?|n°|#)?\s*0*\d{1,3}\s*$", re.I)
GROUPS = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")


def _drop_groups(text: str) -> str:
    """Remove ``(Author)`` and ``[2021]`` parts, which are not in the album title.

    A group that is only a volume number (``(T3)``, ``[12]``) is kept, as is any group when removing
    them would leave nothing.
    """
    def replace(match: re.Match) -> str:
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        return f" {inner} " if VOLUME_GROUP.match(inner) else " "

    stripped = GROUPS.sub(replace, text)
    return stripped if re.search(r"\w", stripped) else text


def query_from_filename(path: str) -> str:
    """``/x/Nains - T01 - Redwin (Author) [2015].cbz`` -> ``Nains 1 Redwin``."""
    stem = os.path.splitext(os.path.basename(path))[0]
    text = re.sub(r"[_\-.]+", " ", _drop_groups(stem))
    text = re.sub(r"\b(\d{1,3})\s+(?:of|de|sur)\s+\d{1,3}\b", r"\1", text, flags=re.I)  # "3 of 6" -> 3
    # "T01" / "Tome 1" / "Vol. 3" -> "1": the sitemap slugs keep only the number
    text = re.sub(r"\b(?:tome|vol|t)\s*0*(\d{1,3})\b", r"\1", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def queries_for(path: str) -> list[str]:
    """Queries to try for a file, from the most to the least specific.

    1. the file name;
    2. the file name behind its folder's name (``Achille Talon/Tome 20 - Viva Papa.pdf``);
    3. series and volume only, from the folder name and from the words before the volume number
       (``Loup Noir 5``, ``Drain 2``): file names often carry authors, years or a collection that
       the album's name does not.

    Queries that add nothing are left out.
    """
    query = query_from_filename(path)
    folder = query_from_filename(os.path.dirname(path) + ".x") if os.path.dirname(path) else ""
    words = query.split()
    queries = [query]
    if folder and not set(folder.lower().split()) <= {w.lower() for w in words}:
        queries.append(f"{folder} {query}")
    return queries


def series_queries(path: str) -> list[str]:
    """``series volume`` queries (``Loup Noir 5``, ``Drain 2``), from the folder name and from the
    words before the volume number in the file name.

    File names often carry authors, years or a collection that the album's name does not, but the
    series and the volume are reliable. Their matches are capped at :data:`SERIES_QUERY_CAP`.
    """
    query = query_from_filename(path)
    wanted = volumes_for(path)
    if not wanted:
        return []
    folder = query_from_filename(os.path.dirname(path) + ".x") if os.path.dirname(path) else ""
    lead = []
    for word in query.split():
        if re.fullmatch(r"\d{1,3}", word):
            break
        lead.append(word)
    queries = []
    for series in (folder, " ".join(lead)):
        text = f"{series} {wanted[0]}" if series else ""
        if text and text not in queries:
            queries.append(text)
    return queries


def small_number(token: str) -> Optional[int]:
    """The volume a token can stand for: ``3``, ``03``, ``V3``, ``#3`` (numbers of more than three digits are years)."""
    marked = re.fullmatch(r"[v#]0*(\d{1,3})", token)
    if marked:
        return int(marked.group(1))
    return int(token) if token.isdigit() and len(token) <= 3 else None


def numeric_title(tokens: list[str]) -> tuple[list[str], Optional[int]]:
    """When a name opens with numbers, which of them are the title and which is the volume: ``(title numbers, volume)``.

    ``14 18 2`` (from ``14-18 - T02``) is the series ``14-18``, volume 2; ``666 1`` is ``666``, volume 1. A single leading
    number is left alone (``100 bullets 1``: the title has words, the caller reads the volume). ``([], None)`` when the
    name does not open with at least two numbers.
    """
    run = 0
    while run < len(tokens) and small_number(tokens[run]) is not None:
        run += 1
    if run < 2:
        return [], None
    return tokens[:run - 1], small_number(tokens[run - 1])


def volumes_for(path: str) -> list[int]:
    """The volume number(s) a file name asks for: the one after a numeric title (``14-18 - T02``: 2, not 14, 18 and 2),
    else every number that looks like a volume."""
    title, volume = numeric_title(query_from_filename(path).lower().split())
    return [volume] if title and volume is not None else volumes_in(query_from_filename(path))


def volumes_in(query: str) -> list[int]:
    """Numbers that look like volume numbers in a file name (``T01``, ``Tome 3``, ``- 2 -``)."""
    found = [int(n) for n in re.findall(r"(?:\bt|\btome\s*|\bvol\.?\s*|\b#)\s*0*(\d{1,3})\b", query, re.I)]
    if not found:
        found = [int(n) for n in re.findall(r"(?<![\d\w])0*(\d{1,3})(?![\d\w])", query)]
    return found


def rerank(query: str, hits: list[tuple[Candidate, float]], wanted: Optional[list[int]] = None) -> list[Match]:
    """Adjust title scores with the volume number found in the file name (``wanted``: the volumes already worked out)."""
    wanted = volumes_in(query) if wanted is None else wanted
    matches = []
    for cand, base in hits:
        score = base
        if wanted and cand.volume is not None:
            if cand.volume in wanted:
                score += VOLUME_MATCH_BONUS
            else:
                score = min(score - VOLUME_MISMATCH_PENALTY, VOLUME_MISMATCH_CAP)
        elif wanted and cand.volume is None:
            score = min(score, NO_VOLUME_CAP)
        matches.append(Match(cand, max(0.0, min(100.0, score)), base))
    return sorted(matches, key=lambda m: m.score, reverse=True)


def find_matches(source, path: str, limit: int = 5) -> list[Match]:
    """The best albums of ``source`` for one file, scored 0-100 and best first. No network access.

    ``source`` is a :class:`~bdnex.importer.source.MetadataSource`. The file name is tried alone and
    behind its folder's name; then ``series volume`` queries are tried, whose matches never score
    above :data:`SERIES_QUERY_CAP` since nothing confirms the title.
    """
    best: dict[str, tuple] = {}  # source_id -> (candidate, best score, found by a series query)
    named, extra = embedded_hint(source, path)
    title_numbers, _ = numeric_title(query_from_filename(path).lower().split())
    by_number = getattr(source, "numeric_title_candidates", None)
    if title_numbers and by_number:  # a title made of numbers (14-18, 666) has no word to look an album up by
        for cand, score in by_number(title_numbers, limit=limit * 4):
            best[cand.source_id] = (cand, score, False)
    for text, by_series in ([(t, False) for t in queries_for(path)] + [(t, True) for t in series_queries(path)]
                            + [(t, True) for t in extra]):
        for cand, score in source.candidates(text, limit=limit):
            if cand.source_id not in best or score > best[cand.source_id][1]:
                best[cand.source_id] = (cand, score, by_series)
    series_only = {sid for sid, (_, _, by_series) in best.items() if by_series}
    hits = sorted(((c, sc) for c, sc, _ in best.values()), key=lambda hit: hit[1], reverse=True)[:limit + 3]
    matches = rerank(query_from_filename(path), hits, volumes_for(path))  # volumes are read from the file name only
    matches = [replace(m, score=min(m.score, SERIES_QUERY_CAP)) if m.candidate.source_id in series_only else m
               for m in matches]
    matches = sorted(matches, key=lambda m: m.score, reverse=True)
    if named is not None:  # the file itself names its album: that is the first choice, whatever the file name says
        matches = [Match(named, 100.0, 100.0)] + [m for m in matches if m.candidate.source_id != named.source_id]
    return matches[:limit]


def embedded_hint(source, path: str):
    """What the file's own ComicInfo.xml says about its album: ``(candidate or None, extra queries)``.

    A ``Web`` field with the address of an album page (bdnex writes it) names the album exactly; a ``Series`` and a
    ``Number`` give a ``series number`` query like those made from the file name. ``match.use_embedded: false``
    switches this off.
    """
    if not getattr(source, "use_embedded", True):
        return None, []
    from bdnex.library.scan import read_embedded

    embedded = read_embedded(path)
    if not embedded:
        return None, []
    from_url = getattr(source, "candidate_from_url", None)
    named = from_url(embedded.get("Web", "")) if from_url else None
    series, number = embedded.get("Series"), embedded.get("Number") or embedded.get("Volume")
    extra = [f"{series} {number}"] if series and number and str(number).isdigit() else []
    return named, extra


def recommend(score: float, strong: float, medium: float) -> Recommendation:
    if score >= strong:
        return Recommendation.STRONG
    if score >= medium:
        return Recommendation.MEDIUM
    return Recommendation.NONE


__all__ = ["Match", "Recommendation", "query_from_filename", "find_matches", "queries_for", "series_queries", "volumes_in", "rerank", "recommend"]
