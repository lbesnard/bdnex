"""Score filename -> album candidates and turn them into a recommendation."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from bdnex.importer.source import Candidate

logger = logging.getLogger(__name__)

# What the adjustments after the title score are worth, in points of the 0-100 score (``match.volume_bonus`` and the other options override them).
DEFAULT_WEIGHTS = {"volume_match": 5.0, "volume_mismatch": 15.0, "year_match": 2.0, "author_match": 3.0}
VOLUME_MATCH_BONUS = DEFAULT_WEIGHTS["volume_match"]  # (kept for the code and tests that name them)
VOLUME_MISMATCH_PENALTY = DEFAULT_WEIGHTS["volume_mismatch"]
YEAR_MATCH_BONUS = DEFAULT_WEIGHTS["year_match"]
AUTHOR_MATCH_BONUS = DEFAULT_WEIGHTS["author_match"]
MIN_MARGIN = 10.0  # a first choice with another album this close behind is not sure
SERIES_QUERY_CAP = 89.0  # matched on series + volume only: always ask, never apply silently
NO_VOLUME_CAP = 85.0  # the file has a volume but the album has none (e.g. an album named like the series)
VOLUME_MISMATCH_CAP = 85.0  # a wrong volume is never a strong match, whatever the title says
INTEGRAL_BONUS = 8.0  # the file says "intégrale" (and gives no volume) and the album is one
INTEGRAL_NUMBERED_PENALTY = 8.0  # ... and the album is a single tome of the series: not the collected edition
INTEGRAL_VOLUME_BONUS = 3.0  # ... and the album is one volume of several collected ones (`INT2`) while the file does not say which


class Recommendation(Enum):
    STRONG = "strong"  # apply without asking
    MEDIUM = "medium"  # ask the user
    NONE = "none"  # no usable match


@dataclass(frozen=True)
class Match:
    candidate: Candidate
    score: float  # 0-100, after the volume adjustment
    base_score: float  # plain title similarity
    doubt: str = ""  # why it must not be applied unasked even with a strong score ("" = no reason to doubt it)
    parts: tuple[str, ...] = ()  # what moved the score after the title score, in words (`bdnex candidates` shows them)


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
    text = re.sub(r"(?<=\d)(?:lq|hq|hd|sd)\b", "", text, flags=re.I)  # a scan's quality tag glued to the number: `Death_Note_-_003lq`
    text = re.sub(r"\b(\d{1,3})\s+(?:of|de|sur)\s+\d{1,3}\b", r"\1", text, flags=re.I)  # "3 of 6" -> 3
    # "T01" / "Tome 1" / "Vol. 3" -> "1": the sitemap slugs keep only the number
    text = re.sub(r"\b(?:tome|vol|t)\s*0*(\d{1,3})\b", r"\1", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


VOLUME_MARKER = re.compile(r"\b(?:tome|vol|t)\s*0*(\d{1,3})\b", re.I)


def explicit_volume(path: str) -> Optional[int]:
    """The volume a file name SAYS it is: ``T01``, ``Tome 1``, ``Vol. 3``. A bare number may be part of the title (``CYCLE 2 - Tome 01``
    is volume 1 of a series whose second cycle this is), so when the name says it, that number wins. ``None``: it does not say."""
    stem = os.path.splitext(os.path.basename(path))[0]
    found = VOLUME_MARKER.search(re.sub(r"[_\-.]+", " ", _drop_groups(stem)))
    return int(found.group(1)) if found else None


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
    said = explicit_volume(path)
    if said is not None:
        return [said]
    title, volume = numeric_title(query_from_filename(path).lower().split())
    return [volume] if title and volume is not None else volumes_in(query_from_filename(path))


def volumes_in(query: str) -> list[int]:
    """Numbers that look like volume numbers in a file name (``T01``, ``Tome 3``, ``- 2 -``)."""
    found = [int(n) for n in re.findall(r"(?:\bt|\btome\s*|\bvol\.?\s*|\b#)\s*0*(\d{1,3})\b", query, re.I)]
    if not found:
        found = [int(n) for n in re.findall(r"(?<![\d\w])0*(\d{1,3})(?![\d\w])", query)]
    if not found:  # last resort: the number glued to the title (``SOA1``, ``SonOfAnarchy6``)
        found = [int(n) for n in re.findall(r"(?<=[^\W\d_])0*(\d{1,3})\b", query)][-1:]
    return found


INTEGRAL_WORD = re.compile(r"\bint[eé]grales?\b", re.I)
INTEGRAL_ADDRESS = re.compile(r"-INT\d*(?:-|\.html)")  # Bédéthèque's address of a collected edition: ...-INT-...
INTEGRAL_NUMBER = re.compile(r"-INT(\d+)(?:-|\.html)")  # ... of the second of several: ...-INT2-...


def asks_for_integral(query: str) -> bool:
    """The file name says it is an intégrale (a collected edition)."""
    return bool(INTEGRAL_WORD.search(query))


def is_integral(cand: Candidate) -> bool:
    """The album is a collected edition: its address says ``INT``, or its name says intégrale."""
    return bool(INTEGRAL_ADDRESS.search(cand.url or "")) or bool(INTEGRAL_WORD.search(cand.name or ""))


def integral_number(cand: Candidate) -> Optional[int]:
    """Which of several collected editions the album is (``INT2``: the second), ``None`` for the one (`INT`, the whole series)."""
    found = INTEGRAL_NUMBER.search(cand.url or "")
    return int(found.group(1)) if found else None


def weight(source, name: str) -> float:
    """What an adjustment is worth: the ``match.*_bonus`` and ``*_penalty`` options of the configuration, else :data:`DEFAULT_WEIGHTS`."""
    return float((getattr(source, "weights", None) or {}).get(name, DEFAULT_WEIGHTS[name]))


def rerank(query: str, hits: list[tuple[Candidate, float]], wanted: Optional[list[int]] = None,
           source=None, integral: Optional[bool] = None) -> list[Match]:
    """Adjust title scores with the volume number found in the file name (``wanted``: the volumes already worked out)."""
    wanted = volumes_in(query) if wanted is None else wanted
    bonus, penalty = weight(source, "volume_match"), weight(source, "volume_mismatch")
    if integral is None:
        integral = asks_for_integral(query)  # (a word in parentheses is gone from the query: `find_matches` reads it in the whole name)
    integral_exists = bool(integral) and any(is_integral(cand) for cand, _ in hits)
    matches = []
    for cand, base in hits:
        score, parts = base, []
        if wanted and cand.volume is not None:
            if cand.volume in wanted:
                score += bonus
                parts.append(f"volume {cand.volume} asked for and found: +{bonus:g}")
            else:
                score = min(score - penalty, VOLUME_MISMATCH_CAP)
                parts.append(f"the file says volume {', '.join(map(str, wanted[:2]))}, the album is volume {cand.volume}: "
                             f"-{penalty:g}, at most {VOLUME_MISMATCH_CAP:g}")
        elif wanted and cand.volume is None and integral and is_integral(cand) and integral_number(cand) in wanted:
            score += bonus  # "Intégrale 2" and the album is `INT2`
            parts.append(f"intégrale {integral_number(cand)} asked for and found: +{bonus:g}")
        elif wanted and cand.volume is None:
            if score > NO_VOLUME_CAP:
                parts.append(f"the file has a volume, the album has none: at most {NO_VOLUME_CAP:g}")
            score = min(score, NO_VOLUME_CAP)
        if integral and not wanted:  # "Universal War One (Intégrale).cbz": the collected edition, not tome 5 that has the same title words
            if is_integral(cand):
                points = INTEGRAL_BONUS if integral_number(cand) is None else INTEGRAL_VOLUME_BONUS  # the whole one, or one of several
                score += points
                which = "" if integral_number(cand) is None else f" (one of several, number {integral_number(cand)})"
                parts.append(f"the file is an intégrale and so is the album{which}: +{points:g}")
            elif cand.volume is not None and integral_exists:  # (without one among the candidates, a penalty would only lift unrelated albums)
                score -= INTEGRAL_NUMBERED_PENALTY
                parts.append(f"the file is an intégrale, the album is tome {cand.volume}: -{INTEGRAL_NUMBERED_PENALTY:g}")
        matches.append(Match(cand, max(0.0, min(100.0, score)), base, parts=tuple(parts)))
    return sorted(matches, key=lambda m: m.score, reverse=True)


def adjust_by_facts(matches: list[Match], path: str, embedded: Optional[dict], facts_of, source=None) -> list[Match]:
    """Rank candidates whose page was read by what it says: a year or an author that agrees earns a few points. Nothing is
    ever taken away: a candidate whose page was never read has no facts, so a penalty would push every album that is
    known below the ones that are not (measured on a real library: it moved correct matches to wrong ones). A
    contradiction is dealt with by :func:`contradicted`, which only keeps a match from being applied unasked."""
    from bdnex.importer import confirm

    stem = os.path.splitext(os.path.basename(path))[0]
    years = confirm.years_named(stem, embedded or {})
    year_bonus, author_bonus = weight(source, "year_match"), weight(source, "author_match")
    adjusted = []
    for match in matches:
        facts = facts_of(match.candidate)
        if not facts:
            adjusted.append(match)
            continue
        score, parts = match.score, list(match.parts)
        if confirm.year_agrees(years, facts.get("year")):
            score += year_bonus
            parts.append(f"the year {facts['year']} agrees: +{year_bonus:g}")
        shared = confirm.author_words(stem, facts) & confirm.credit_words(facts)
        if shared:
            score += author_bonus
            parts.append(f"an author ({', '.join(sorted(shared)[:2])}) agrees: +{author_bonus:g}")
        adjusted.append(replace(match, score=max(0.0, min(100.0, score)), parts=tuple(parts)) if parts != list(match.parts) else match)
    return sorted(adjusted, key=lambda m: m.score, reverse=True)


def find_matches(source, path: str, limit: int = 5) -> list[Match]:
    """The best albums of ``source`` for one file, scored 0-100 and best first. No network access.

    ``source`` is a :class:`~bdnex.importer.source.MetadataSource`. The file name is tried alone and
    behind its folder's name; then ``series volume`` queries are tried, whose matches never score
    above :data:`SERIES_QUERY_CAP` since nothing confirms the title.
    """
    best: dict[str, tuple] = {}  # source_id -> (candidate, best score, found by a series query)
    integral_file = asks_for_integral(os.path.splitext(os.path.basename(path))[0])
    embedded = _embedded_of(source, path) or {}  # {}: looked at, nothing there (not read twice)
    named, extra = embedded_hint(source, path, embedded)
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
    ranked = sorted(((c, sc) for c, sc, _ in best.values()), key=lambda hit: hit[1], reverse=True)
    hits = ranked[:limit + 3]
    matches = rerank(query_from_filename(path), hits, volumes_for(path), source,  # volumes are read from the file name only
                     integral=integral_file)
    matches = [replace(m, score=min(m.score, SERIES_QUERY_CAP),
                       parts=(*m.parts, f"found by series and volume only: at most {SERIES_QUERY_CAP:g}") if m.score > SERIES_QUERY_CAP else m.parts)
               if m.candidate.source_id in series_only else m for m in matches]
    matches = sorted(matches, key=lambda m: m.score, reverse=True)
    facts_of = getattr(source, "facts_of", None)
    if facts_of is not None:
        matches = adjust_by_facts(matches, path, embedded, facts_of, source)
    matches = _doubts(source, path, matches, embedded, named)
    if named is not None:  # the file itself names its album: that is the first choice, whatever the file name says
        matches = [Match(named, 100.0, 100.0)] + [m for m in matches if m.candidate.source_id != named.source_id]
    return matches[:limit]


def _doubts(source, path: str, matches: list[Match], embedded: Optional[dict], named) -> list[Match]:
    """Give the first choice a reason to be asked about when one exists, whatever its score:

    * **a close second**: another album is within ``min_margin`` points (measured on a real library: with a margin of
      10, the wrong matches among those scoring 90 or more went from 11 in 2,876 to 2, and the 257 files asked about
      instead were right 248 times);
    * **a strong contradiction**: the file's own tags name another series, or it is a supplement or an integral for a
      numbered tome (see :mod:`bdnex.importer.confirm`).

    A file that names its album by its address (``named``) is not doubted.
    """
    if not matches or (named is not None and matches[0].candidate.source_id == named.source_id):
        return matches
    first, reason = matches[0], ""
    margin = getattr(source, "min_margin", MIN_MARGIN)
    second = next((m for m in matches[1:] if m.candidate.source_id != first.candidate.source_id), None)
    if margin and second is not None and first.score - second.score < margin:
        reason = f"another album is only {first.score - second.score:.0f} point(s) behind"
    if not reason:
        from bdnex.importer import confirm

        album = confirm.album_of_candidate(source, first.candidate)
        if album is not None:
            reason = "; ".join(confirm.assess(path, album, embedded).strong)
    return [replace(first, doubt=reason)] + matches[1:] if reason else matches


def _embedded_of(source, path: str) -> Optional[dict]:
    if not getattr(source, "use_embedded", True):
        return None
    from bdnex.library.scan import read_embedded

    return read_embedded(path)


def embedded_hint(source, path: str, embedded: Optional[dict] = None):
    """What the file's own ComicInfo.xml says about its album: ``(candidate or None, extra queries)``.

    A ``Web`` field with the address of an album page (bdnex writes it) names the album exactly, **if that album is in
    the album list** (a stale or wrong address is not trusted); a ``Series`` and a
    ``Number`` give a ``series number`` query like those made from the file name. ``match.use_embedded: false``
    switches this off.
    """
    if not getattr(source, "use_embedded", True):
        return None, []
    if embedded is None:
        embedded = _embedded_of(source, path)
    if not embedded:
        return None, []
    from_url = getattr(source, "known_candidate_from_url", None) or getattr(source, "candidate_from_url", None)
    named = from_url(embedded.get("Web", "")) if from_url else None
    if named is None and embedded.get("Web"):
        logger.debug(f"{path}: its Web address {embedded['Web']!r} is not an album of the album list: not trusted")
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
