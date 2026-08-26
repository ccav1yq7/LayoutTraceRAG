"""Reciprocal Rank Fusion — combine multiple ranked lists into one.

RRF is rank-based (not score-based), so it fuses a dense semantic ranking and a
lexical/BM25 ranking without needing their scores to be comparable.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence


def reciprocal_rank_fusion(
    rankings: Sequence[Iterable[str]], k: int = 60, weights: Sequence[float] | None = None
) -> list[tuple[str, float]]:
    """Fuse ranked id-lists into one ``(id, score)`` list, best first.

    ``score(id) = sum_r  weight_r / (k + rank_r(id))`` where ``rank`` is 1-based.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights length must match rankings length")

    scores: dict[str, float] = {}
    for ranking, w in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w / (k + rank)

    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
