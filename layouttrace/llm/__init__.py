"""LLM engines and the factory that picks one based on the environment."""
from __future__ import annotations

from ..config import Config
from .base import LLMEngine
from .heuristic import HeuristicEngine

__all__ = ["LLMEngine", "HeuristicEngine", "get_engine"]


def get_engine(config: Config | None = None) -> LLMEngine:
    """Return a LangChain-backed engine when a provider key is set, else the
    heuristic engine so the pipeline always runs."""
    config = config or Config()
    if config.has_llm_key:
        try:
            from .langchain_engine import LangChainEngine

            return LangChainEngine(config.llm_provider, config.llm_model)
        except Exception:  # missing optional dep or provider init failure
            pass
    return HeuristicEngine()
