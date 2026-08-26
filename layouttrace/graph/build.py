"""Assemble the LangGraph state machine.

    plan → retrieve → grade ─┬─(sufficient|budget spent)→ generate → verify → END
                             └─(insufficient)───────────→ refine → retrieve  (loop)
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from ..config import Config
from ..llm.base import LLMEngine
from ..retrieval.hybrid import HybridRetriever
from ..retrieval.rerank import Reranker
from ..types import Answer
from .nodes import build_nodes, route_after_grade, route_after_verify
from .state import GraphState


def build_graph(
    retriever: HybridRetriever,
    engine: LLMEngine,
    config: Config | None = None,
    reranker: Reranker | None = None,
):
    config = config or Config()
    nodes = build_nodes(retriever, engine, config, reranker=reranker)

    g = StateGraph(GraphState)
    stages = ["plan", "retrieve"] + (["rerank"] if reranker is not None else []) + [
        "grade", "refine", "generate", "verify",
    ]
    for name in stages:
        g.add_node(name, nodes[name])
    g.set_entry_point("plan")

    # plan → retrieve → [rerank] → grade
    post_retrieve = "rerank" if reranker is not None else "grade"
    g.add_edge("plan", "retrieve")
    g.add_edge("retrieve", post_retrieve)
    if reranker is not None:
        g.add_edge("rerank", "grade")
    # grade ─(sufficient|budget)→ generate ; ─(else)→ refine → retrieve
    g.add_conditional_edges(
        "grade", route_after_grade(config), {"generate": "generate", "refine": "refine"}
    )
    g.add_edge("refine", "retrieve")
    g.add_edge("generate", "verify")
    # verify ─(grounded | regen budget spent)→ END ; ─(under-grounded)→ generate
    g.add_conditional_edges(
        "verify", route_after_verify(config), {"generate": "generate", "end": END}
    )
    return g.compile()


def answer_question(graph, question: str) -> Answer:
    """Run the compiled graph and package the result as an :class:`Answer`."""
    final = graph.invoke({"question": question})
    return Answer(
        question=question,
        text=final.get("answer", ""),
        citations=final.get("citations", []),
        # honour the Self-RAG verdict from the verify node (not mere citation presence)
        grounded=bool(final.get("grounded", bool(final.get("citations")))),
        iterations=final.get("iteration", 0) + 1,
    )
