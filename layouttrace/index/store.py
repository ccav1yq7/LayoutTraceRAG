"""Evidence stores that back the hybrid retriever.

- :class:`InMemoryStore` — zero external services (cosine + BM25/overlap); great
  for tests, demos and small corpora.
- :class:`LanceStore` — LanceDB with native vector + full-text (FTS) search;
  the production path (multimodal-first, embedded, single file).

Both expose ``dense`` / ``lexical`` :class:`~layouttrace.retrieval.hybrid.Searcher`
adapters and a ``lookup`` callable, wired together by :func:`build_retriever`.
"""
from __future__ import annotations

import math
import re
from typing import Callable

from ..config import Config
from ..retrieval.hybrid import HybridRetriever
from ..types import EvidenceNode
from .embed import Embedder, get_embedder

_TOKEN = re.compile(r"[\w一-鿿]+")


def _tok(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


class _FnSearcher:
    def __init__(self, fn: Callable[[str, int], list[str]]) -> None:
        self._fn = fn

    def search(self, query: str, k: int) -> list[str]:
        return self._fn(query, k)


class InMemoryStore:
    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder or get_embedder()
        self._nodes: dict[str, EvidenceNode] = {}
        self._vecs: dict[str, list[float]] = {}

    def add(self, nodes: list[EvidenceNode]) -> None:
        vecs = self._embedder.embed_documents([n.text for n in nodes])
        for n, v in zip(nodes, vecs):
            self._nodes[n.id] = n
            self._vecs[n.id] = v

    def _dense(self, query: str, k: int) -> list[str]:
        q = self._embedder.embed_query(query)
        qn = math.sqrt(sum(x * x for x in q)) or 1.0

        def cos(v: list[float]) -> float:
            vn = math.sqrt(sum(x * x for x in v)) or 1.0
            return sum(a * b for a, b in zip(q, v)) / (qn * vn)

        return [i for i, _ in sorted(
            ((i, cos(v)) for i, v in self._vecs.items()), key=lambda kv: kv[1], reverse=True
        )[:k]]

    def _lexical(self, query: str, k: int) -> list[str]:
        qset = set(_tok(query))
        scored = [(i, sum(1 for t in _tok(n.text) if t in qset)) for i, n in self._nodes.items()]
        scored = [s for s in scored if s[1] > 0] or scored
        return [i for i, _ in sorted(scored, key=lambda kv: kv[1], reverse=True)[:k]]

    @property
    def dense(self) -> _FnSearcher:
        return _FnSearcher(self._dense)

    @property
    def lexical(self) -> _FnSearcher:
        return _FnSearcher(self._lexical)

    def lookup(self, node_id: str) -> EvidenceNode | None:
        return self._nodes.get(node_id)


class LanceStore:
    """LanceDB-backed store (native vector + FTS). Requires the ``retrieval`` extra."""

    def __init__(self, config: Config, embedder: Embedder | None = None) -> None:
        import lancedb  # noqa: F401  (raises if extra not installed)

        self._config = config
        self._embedder = embedder or get_embedder(config.embed_model)
        self._embedding_id = getattr(self._embedder, "model_id", getattr(self._embedder, "model_name", config.embed_model))
        self._db = __import__("lancedb").connect(config.db_path)
        self._table = None
        self._nodes: dict[str, EvidenceNode] = {}
        if config.table in self._db.table_names():  # reopen a persisted index
            self._table = self._db.open_table(config.table)
            if "embedding_id" not in self._table.schema.names:
                raise ValueError("Legacy index has no embedding identity; reindex into a new LT_TABLE")
            for r in self._table.to_arrow().to_pylist():
                if r.get("embedding_id") != self._embedding_id:
                    raise ValueError("Index embedding identity is missing or different. Reindex into a new LT_TABLE; the old table was not modified.")
                n = EvidenceNode(id=r["id"], video_id=r["video_id"], modality=r["modality"],
                                 text=r["text"], start_s=r["start_s"], end_s=r["end_s"])
                self._nodes[n.id] = n

    def add(self, nodes: list[EvidenceNode]) -> None:
        if not nodes:
            return
        vecs = self._embedder.embed_documents([n.text for n in nodes])
        if len(vecs) != len(nodes) or len({len(v) for v in vecs}) != 1:
            raise ValueError("Embedding count or dimensions are inconsistent")
        rows = [
            {"id": n.id, "video_id": n.video_id, "modality": n.modality, "text": n.text,
             "start_s": n.start_s, "end_s": n.end_s, "vector": v,
             "embedding_id": self._embedding_id}
            for n, v in zip(nodes, vecs)
        ]
        if self._config.table in self._db.table_names():
            self._table = self._db.open_table(self._config.table)
            self._table.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(rows)
        else:
            self._table = self._db.create_table(self._config.table, data=rows)
        self._table.create_fts_index("text", replace=True)
        for n in nodes:
            self._nodes[n.id] = n

    def _dense(self, query: str, k: int) -> list[str]:
        if self._table is None:
            return []
        q = self._embedder.embed_query(query)
        return [r["id"] for r in self._table.search(q).limit(k).to_list()]

    def _lexical(self, query: str, k: int) -> list[str]:
        if self._table is None:
            return []
        return [r["id"] for r in self._table.search(query, query_type="fts").limit(k).to_list()]

    @property
    def dense(self) -> _FnSearcher:
        return _FnSearcher(self._dense)

    @property
    def lexical(self) -> _FnSearcher:
        return _FnSearcher(self._lexical)

    def lookup(self, node_id: str) -> EvidenceNode | None:
        return self._nodes.get(node_id)


def build_retriever(store, config: Config | None = None, visual=None) -> HybridRetriever:
    config = config or Config()
    return HybridRetriever(
        dense=store.dense,
        lexical=store.lexical,
        node_lookup=store.lookup,
        candidate_k=config.candidate_k,
        rrf_k=config.rrf_k,
        visual=visual,
        visual_weight=config.visual_weight,
    )
