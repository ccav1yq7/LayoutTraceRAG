"""Second-stage reranking: a cross-encoder re-scores the fused candidates.

Hybrid recall is fast but coarse; a cross-encoder reads each (query, node) pair
jointly and reorders for precision. Falls back to identity when the model isn't
installed, so the graph always runs.
"""
from __future__ import annotations

from typing import Protocol

from ..config import Config
from ..types import EvidenceNode


class Reranker(Protocol):
    def rerank(self, query: str, nodes: list[EvidenceNode], k: int) -> list[EvidenceNode]: ...


class IdentityReranker(Reranker):
    def rerank(self, query: str, nodes: list[EvidenceNode], k: int) -> list[EvidenceNode]:
        return nodes[:k]


class CrossEncoderReranker(Reranker):
    def __init__(self, model: str = "BAAI/bge-reranker-v2-m3") -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model)

    def rerank(self, query: str, nodes: list[EvidenceNode], k: int) -> list[EvidenceNode]:
        if not nodes:
            return []
        scores = self._model.predict([(query, n.text) for n in nodes])
        order = sorted(range(len(nodes)), key=lambda i: float(scores[i]), reverse=True)
        return [nodes[i] for i in order[:k]]


def get_reranker(config: Config | None = None) -> Reranker | None:
    """Return a reranker when enabled (falling back to identity if the model is
    unavailable), or ``None`` to skip the stage entirely."""
    config = config or Config()
    if not config.use_reranker:
        return None
    try:
        return CrossEncoderReranker(config.rerank_model)
    except Exception:
        return IdentityReranker()
