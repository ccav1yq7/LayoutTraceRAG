"""Node functions for the agentic retrieval graph.

Built by a factory that closes over the retriever, an optional reranker, the LLM
engine and config, so nodes stay pure ``state -> partial_state`` functions.
"""
from __future__ import annotations

import re

from ..config import Config
from ..llm.base import LLMEngine
from ..retrieval.hybrid import HybridRetriever, collect_candidates
from ..retrieval.rerank import Reranker
from ..types import Citation
from .state import GraphState, merge_evidence


def build_nodes(
    retriever: HybridRetriever,
    engine: LLMEngine,
    config: Config,
    reranker: Reranker | None = None,
) -> dict:
    def plan(state: GraphState) -> GraphState:
        queries = engine.expand_query(state["question"]) or [state["question"]]
        return {"query": queries[0], "queries": queries, "iteration": 0, "regen": 0,
                "evidence": [], "notes": [f"plan: {len(queries)} queries (HyDE/multi-query)"]}

    def retrieve(state: GraphState) -> GraphState:
        queries = state.get("queries") or [state["query"]]
        new = collect_candidates(retriever, queries, config.candidate_k, config.rrf_k)
        merged = merge_evidence(new, state.get("evidence", []))[:config.candidate_k]
        if reranker is None:
            merged = merged[:config.top_k]
        return {"evidence": merged,
                "notes": [f"retrieve[{state.get('iteration', 0)}]: {len(queries)}q → {len(merged)} nodes"]}

    def rerank(state: GraphState) -> GraphState:
        if reranker is None:
            return {}
        q = state["question"]
        ranked = reranker.rerank(q, state.get("evidence", []), config.top_k)
        return {"evidence": ranked, "notes": [f"rerank: → {len(ranked)}"]}

    def grade(state: GraphState) -> GraphState:
        # CRAG: score each doc, refine knowledge (drop irrelevant), decide correctively.
        evidence = state.get("evidence", [])
        graded = engine.evaluate_evidence(state["question"], evidence)
        kept = [g.node for g in graded if g.label != "incorrect"]
        n_correct = sum(1 for g in graded if g.label == "correct")
        sufficient = n_correct >= 1
        return {"evidence": kept, "sufficient": sufficient,
                "notes": [f"crag: {n_correct} correct / {len(kept)} kept / {len(evidence)} → "
                          f"{'sufficient' if sufficient else 'corrective refine'}"]}

    def refine(state: GraphState) -> GraphState:
        new_query = engine.refine(state["question"], state.get("evidence", []), state["query"])
        queries = engine.expand_query(new_query) or [new_query]
        return {"query": new_query, "queries": queries,
                "iteration": state.get("iteration", 0) + 1, "notes": [f"refine: {new_query}"]}

    def generate(state: GraphState) -> GraphState:
        evidence = state.get("evidence", [])
        text = engine.generate(state["question"], evidence) if evidence else "证据不足，无法回答。"
        return {"answer": text, "notes": ["generate"]}

    def verify(state: GraphState) -> GraphState:
        # Self-RAG: attribute each claim to evidence; regenerate if under-grounded.
        answer = state.get("answer", "")
        evidence = state.get("evidence", [])
        claims = _split_claims(answer)
        supported_nodes = {}
        n_supported = 0
        for claim in claims:
            times = re.findall(r"\[(\d{2,}:\d{2}:\d{2}-\d{2,}:\d{2}:\d{2})\]", claim)
            refs = [n for n in evidence if n.timecode in times]
            # A timecode shared by multiple videos is ambiguous: do not invent ownership.
            unique = all(sum(n.timecode == t for n in evidence) == 1 for t in times)
            bare = re.sub(r"\[[^\]]+\]", "", claim).strip()
            if bare and refs and unique and len({n.timecode for n in refs}) == len(set(times)) and engine.supported(bare, refs):
                n_supported += 1
                supported_nodes.update({n.id: n for n in refs})
        ratio = n_supported / len(claims) if claims else 0.0
        grounded = bool(claims) and n_supported == len(claims) and ratio >= config.groundedness_threshold
        cited = [Citation.from_node(n) for n in supported_nodes.values()]
        regen = state.get("regen", 0) + (0 if grounded else 1)
        return {"citations": cited, "grounded": grounded, "regen": regen,
                "notes": [f"verify: grounded={ratio:.2f} ({'ok' if grounded else 'regen'})"]}

    return {"plan": plan, "retrieve": retrieve, "rerank": rerank, "grade": grade,
            "refine": refine, "generate": generate, "verify": verify}


_SENT = re.compile(r"[^。！？!?\n.]+[。！？!?.]?(?:[ \t]*\[[0-9: -]+\])*")


def _split_claims(text: str) -> list[str]:
    return [s.strip() for s in _SENT.findall(text) if len(s.strip()) >= 4]


def route_after_grade(config: Config):
    def _route(state: GraphState) -> str:
        if state.get("sufficient") or state.get("iteration", 0) >= config.max_iterations:
            return "generate"
        return "refine"

    return _route


def route_after_verify(config: Config):
    """Regenerate when the answer is under-grounded and the budget allows."""
    def _route(state: GraphState) -> str:
        if state.get("grounded", True) or state.get("regen", 0) > config.max_regen:
            return "end"
        return "generate"

    return _route
