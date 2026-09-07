"""Hybrid retriever: dense + lexical (+ optional visual) rankings fused with RRF.

Decoupled from any storage engine via the :class:`Searcher` protocol, so it can
run on LanceDB in production and on a trivial in-memory searcher in tests. When a
cross-modal ``visual`` searcher is supplied, frames retrieved by their *visual*
content join the fusion alongside dense text and BM25.
"""
from __future__ import annotations

from typing import Callable, Protocol

from ..types import EvidenceNode
from .fusion import reciprocal_rank_fusion


class Searcher(Protocol):
    """Returns node ids ranked best-first for a query."""

    def search(self, query: str, k: int) -> list[str]: ...


def collect_candidates(retriever, queries: list[str], candidate_k: int, rrf_k: int) -> list[EvidenceNode]:
    """Shared query-fusion path for the application graph and benchmark."""
    rankings, lookup = [], {}
    for query in queries:
        hits = retriever.retrieve(query, candidate_k)
        rankings.append([node.id for node in hits])
        lookup.update({node.id: node for node in hits})
    return [lookup[key] for key, _ in reciprocal_rank_fusion(rankings, k=rrf_k)[:candidate_k]]


class HybridRetriever:
    def __init__(
        self,
        dense: Searcher,
        lexical: Searcher,
        node_lookup: Callable[[str], EvidenceNode | None],
        candidate_k: int = 30,
        rrf_k: int = 60,
        weights: tuple[float, float] = (1.0, 1.0),
        visual: Searcher | None = None,
        visual_weight: float = 1.0,
    ) -> None:
        self._dense = dense
        self._lexical = lexical
        self._lookup = node_lookup
        self._candidate_k = candidate_k
        self._rrf_k = rrf_k
        self._weights = weights
        self._visual = visual
        self._visual_weight = visual_weight

    def retrieve(self, query: str, k: int) -> list[EvidenceNode]:
        rankings = [
            self._dense.search(query, self._candidate_k),
            self._lexical.search(query, self._candidate_k),
        ]
        weights = list(self._weights)
        if self._visual is not None:
            rankings.append(self._visual.search(query, self._candidate_k))
            weights.append(self._visual_weight)
        fused = reciprocal_rank_fusion(rankings, k=self._rrf_k, weights=weights)
        out: list[EvidenceNode] = []
        for node_id, _score in fused:
            node = self._lookup(node_id)
            if node is not None:
                out.append(node)
            if len(out) >= k:
                break
        return out
