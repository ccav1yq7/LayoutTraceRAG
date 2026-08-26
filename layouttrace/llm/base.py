"""The reasoning operations the graph needs from a language model.

Kept as a small Protocol so the graph is testable with a heuristic engine and
swappable between OpenAI / Anthropic in production.
"""
from __future__ import annotations

from typing import Protocol

from ..types import EvidenceNode, GradedNode


class LLMEngine(Protocol):
    def plan(self, question: str) -> str:
        """Turn a user question into an initial retrieval query."""

    def expand_query(self, question: str) -> list[str]:
        """Multi-query + HyDE: the question plus alternative phrasings and a
        hypothetical answer, all used as retrieval queries and fused."""

    def grade(self, question: str, evidence: list[EvidenceNode]) -> bool:
        """Decide whether the gathered evidence can answer the question."""

    def evaluate_evidence(
        self, question: str, evidence: list[EvidenceNode]
    ) -> list[GradedNode]:
        """CRAG per-document relevance: label each node correct/ambiguous/incorrect."""

    def refine(self, question: str, evidence: list[EvidenceNode], prev_query: str) -> str:
        """Produce a better retrieval query for the next round."""

    def generate(self, question: str, evidence: list[EvidenceNode]) -> str:
        """Write the grounded answer; must cite evidence by [timecode]."""

    def supported(self, claim: str, evidence: list[EvidenceNode]) -> bool:
        """Self-RAG ISSUP: is this individual claim supported by the evidence?"""
