"""Score filename -> album candidates and turn them into a recommendation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum

from bdnex.importer.source import Candidate

VOLUME_MATCH_BONUS = 5.0
VOLUME_MISMATCH_PENALTY = 15.0


class Recommendation(Enum):
    STRONG = "strong"  # apply without asking
    MEDIUM = "medium"  # ask the user
    NONE = "none"  # no usable match


@dataclass(frozen=True)
class Match:
    candidate: Candidate
    score: float  # 0-100, after the volume adjustment
    base_score: float  # plain title similarity


def query_from_filename(path: str) -> str:
    """``/x/Nains - T01 - Redwin.cbz`` -> ``Nains T01 Redwin``."""
    stem = os.path.splitext(os.path.basename(path))[0]
    text = re.sub(r"[_\-.]+", " ", stem)
    # "T01" / "Tome 1" / "Vol. 3" -> "1": the sitemap slugs keep only the number
    text = re.sub(r"\b(?:tome|vol|t)\s*0*(\d{1,3})\b", r"\1", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def volumes_in(query: str) -> list[int]:
    """Numbers that look like volume numbers in a file name (``T01``, ``Tome 3``, ``- 2 -``)."""
    found = [int(n) for n in re.findall(r"(?:\bt|\btome\s*|\bvol\.?\s*|\b#)\s*0*(\d{1,3})\b", query, re.I)]
    if not found:
        found = [int(n) for n in re.findall(r"(?<![\d\w])0*(\d{1,3})(?![\d\w])", query)]
    return found


def rerank(query: str, hits: list[tuple[Candidate, float]]) -> list[Match]:
    """Adjust title scores with the volume number found in the file name."""
    wanted = volumes_in(query)
    matches = []
    for cand, base in hits:
        score = base
        if wanted and cand.volume is not None:
            score += VOLUME_MATCH_BONUS if cand.volume in wanted else -VOLUME_MISMATCH_PENALTY
        matches.append(Match(cand, max(0.0, min(100.0, score)), base))
    return sorted(matches, key=lambda m: m.score, reverse=True)


def recommend(score: float, strong: float, medium: float) -> Recommendation:
    if score >= strong:
        return Recommendation.STRONG
    if score >= medium:
        return Recommendation.MEDIUM
    return Recommendation.NONE


__all__ = ["Match", "Recommendation", "query_from_filename", "volumes_in", "rerank", "recommend"]
