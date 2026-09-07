"""Visual multimodal retrieval.

A cross-modal encoder (CLIP / SigLIP) puts frame images and text queries in one
space, so a text question can retrieve **frames by their visual content**, not
just by OCR text. (For document-page-style visual retrieval, ColPali-style
late-interaction is a drop-in alternative behind the same ``VisionEmbedder``.)

An explicit hash-demo encoder supports model-free tests; real-model failures raise.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol

from ..retrieval.hybrid import Searcher


class VisionEmbedder(Protocol):
    def embed_text(self, text: str) -> list[float]: ...
    def embed_image(self, image: Any) -> list[float]: ...


class CrossModalEmbedder(VisionEmbedder):
    """CLIP/SigLIP via sentence-transformers — images and text share one space."""

    def __init__(self, model: str = "clip-ViT-B-32") -> None:
        from sentence_transformers import SentenceTransformer

        self._m = SentenceTransformer(model)

    def embed_text(self, text: str) -> list[float]:
        return self._m.encode(text).tolist()

    def embed_image(self, image: Any) -> list[float]:
        return self._m.encode(image).tolist()


class HashVisionEmbedder(VisionEmbedder):
    """Deterministic offline fallback (no semantics; keeps things runnable)."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _hash(self, key: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in key.lower().split():
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_text(self, text: str) -> list[float]:
        return self._hash(text)

    def embed_image(self, image: Any) -> list[float]:
        return self._hash(str(image))


def get_vision_embedder(model: str = "clip-ViT-B-32") -> VisionEmbedder:
    if model == "hash-demo":
        return HashVisionEmbedder()
    try:
        return CrossModalEmbedder(model)
    except Exception as exc:
        raise RuntimeError(f"Cannot load visual model {model!r}") from exc


class VisualSearcher(Searcher):
    """Cross-modal frame search: embed the text query, cosine-rank frame vectors."""

    def __init__(self, embedder: VisionEmbedder | None = None) -> None:
        self._embedder = embedder or get_vision_embedder()
        self._vecs: dict[str, list[float]] = {}

    def add(self, node_id: str, vector: list[float]) -> None:
        self._vecs[node_id] = vector

    def add_image(self, node_id: str, image: Any) -> None:
        self._vecs[node_id] = self._embedder.embed_image(image)

    def search(self, query: str, k: int) -> list[str]:
        if not self._vecs:
            return []
        q = self._embedder.embed_text(query)
        qn = math.sqrt(sum(x * x for x in q)) or 1.0

        def cos(v: list[float]) -> float:
            vn = math.sqrt(sum(x * x for x in v)) or 1.0
            return sum(a * b for a, b in zip(q, v)) / (qn * vn)

        return [i for i, _ in sorted(
            ((i, cos(v)) for i, v in self._vecs.items()), key=lambda kv: kv[1], reverse=True
        )[:k]]
