"""LangGraph state for the agentic retrieval loop.

``evidence`` is the single working set of retrieved nodes; it is accumulated
manually in the ``retrieve`` node and then reordered / refined in place by the
``rerank`` and ``grade`` nodes, so downstream nodes always read one coherent list.
"""
from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict

from ..types import Citation, EvidenceNode


def merge_evidence(prev: list[EvidenceNode], new: list[EvidenceNode]) -> list[EvidenceNode]:
    """Accumulate across retrieval rounds, de-duped by id, order preserved."""
    seen = {n.id for n in prev}
    return prev + [n for n in new if n.id not in seen]


class GraphState(TypedDict, total=False):
    question: str
    queries: list[str]                 # plan output: original + expansions (HyDE / multi-query)
    query: str                         # primary query (kept for logging / refine)
    evidence: list[EvidenceNode]       # working set (accumulated, reranked, refined)
    iteration: int
    regen: int                         # groundedness-driven regenerate count
    sufficient: bool
    grounded: bool
    answer: str
    citations: list[Citation]
    notes: Annotated[list[str], add]     # accumulates → full execution trace
