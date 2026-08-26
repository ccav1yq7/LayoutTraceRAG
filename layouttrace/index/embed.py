"""Embeddings — BGE-M3 in production, a deterministic hash embedder as a
zero-dependency fallback for tests and demos."""
from __future__ import annotations

import hashlib
import math
from typing import Protocol


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class HashEmbedder(Embedder):
    """Cheap, deterministic bag-of-hashed-tokens embedding — no model download.

    Not semantically strong; it only exists so the pipeline is runnable offline.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in text.lower().split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def get_embedder(model: str = "BAAI/bge-m3") -> Embedder:
    """Return a BGE-M3 embedder if the optional stack is installed, else the
    hash fallback."""
    try:
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name=model)  # type: ignore[return-value]
    except Exception:
        return HashEmbedder()
