"""LLM engines and the factory that picks one based on the environment."""
from __future__ import annotations

from ..config import Config
from .base import LLMEngine
from .heuristic import HeuristicEngine

__all__ = ["LLMEngine", "HeuristicEngine", "get_engine"]


def get_engine(config: Config | None = None) -> LLMEngine:
    """Use the configured provider, or local heuristic generation when no key is set.

    A configured provider failing to initialize is an error, not permission to silently switch.
    """
    config = config or Config()
    if config.has_llm_key:
        try:
            from .langchain_engine import LangChainEngine

            return LangChainEngine(config.llm_provider, config.llm_model)
        except Exception as exc:
            raise RuntimeError("Configured LLM could not be initialized; check the llm extra and provider settings") from exc
    return HeuristicEngine()
