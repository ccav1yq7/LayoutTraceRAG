"""Runtime configuration, overridable via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # retrieval
    embed_model: str = os.environ.get("LT_EMBED_MODEL", "BAAI/bge-m3")
    rerank_model: str = os.environ.get("LT_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    db_path: str = os.environ.get("LT_DB_PATH", "./.layouttrace/lancedb")
    table: str = os.environ.get("LT_TABLE", "evidence")
    top_k: int = _int("LT_TOP_K", 8)
    candidate_k: int = _int("LT_CANDIDATE_K", 30)
    rrf_k: int = _int("LT_RRF_K", 60)
    use_reranker: bool = os.environ.get("LT_USE_RERANKER", "0") == "1"
    visual_weight: float = float(os.environ.get("LT_VISUAL_WEIGHT", "1.0"))  # RRF weight of the cross-modal frame ranking

    # agentic loop
    max_iterations: int = _int("LT_MAX_ITERS", 3)
    max_regen: int = _int("LT_MAX_REGEN", 1)          # groundedness-driven regenerate budget
    groundedness_threshold: float = float(os.environ.get("LT_GROUNDEDNESS", "0.6"))

    # llm
    llm_provider: str = os.environ.get("LT_LLM_PROVIDER", "openai")  # openai | anthropic
    llm_model: str = os.environ.get("LT_LLM_MODEL", "gpt-4o-mini")

    # video ingestion
    asr_model: str = os.environ.get("LT_ASR_MODEL", "small")
    asr_language: str | None = os.environ.get("LT_ASR_LANG") or None
    chunk_seconds: float = float(os.environ.get("LT_CHUNK_SECONDS", "30"))
    scene_threshold: float = float(os.environ.get("LT_SCENE_THRESHOLD", "0.4"))

    @property
    def has_llm_key(self) -> bool:
        key = "OPENAI_API_KEY" if self.llm_provider == "openai" else "ANTHROPIC_API_KEY"
        return bool(os.environ.get(key))
