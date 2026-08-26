"""Indexing: embeddings + evidence stores (LanceDB hybrid / in-memory)."""
from __future__ import annotations

from .embed import Embedder, HashEmbedder, get_embedder
from .store import InMemoryStore, LanceStore, build_retriever
from .vision import (
    CrossModalEmbedder,
    HashVisionEmbedder,
    VisionEmbedder,
    VisualSearcher,
    get_vision_embedder,
)

__all__ = [
    "Embedder", "HashEmbedder", "get_embedder",
    "InMemoryStore", "LanceStore", "build_retriever",
    "VisionEmbedder", "CrossModalEmbedder", "HashVisionEmbedder",
    "get_vision_embedder", "VisualSearcher",
]
