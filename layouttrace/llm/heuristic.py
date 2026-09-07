"""A no-LLM engine: keeps the whole graph runnable (and testable) without any
API key. Grades by evidence coverage and produces an extractive, cited answer.
"""
from __future__ import annotations

import re

from ..types import EvidenceNode, GradedNode
from .base import LLMEngine

_TOKEN = re.compile(r"[\w一-鿿]+")


def _keywords(text: str) -> set[str]:
    words = {t.lower() for t in _TOKEN.findall(text) if len(t) >= 2}
    for span in re.findall(r"[一-鿿]+", text):
        words.update(span[i:i+2] for i in range(len(span)-1))
    return words


class HeuristicEngine(LLMEngine):
    def __init__(self, min_nodes: int = 2, top_answer: int = 3) -> None:
        self._min_nodes = min_nodes
        self._top_answer = top_answer

    def plan(self, question: str) -> str:
        return question.strip()

    def expand_query(self, question: str) -> list[str]:
        # deterministic multi-query: original + a keyword-only query
        kw = sorted(_keywords(question))
        variants = [question.strip()]
        if kw:
            variants.append(" ".join(kw))
        # de-dupe, preserve order
        seen, out = set(), []
        for v in variants:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def grade(self, question: str, evidence: list[EvidenceNode]) -> bool:
        if len(evidence) < self._min_nodes:
            return False
        q = _keywords(question)
        covered = any(_keywords(n.text) & q for n in evidence)
        return covered

    def evaluate_evidence(
        self, question: str, evidence: list[EvidenceNode]
    ) -> list[GradedNode]:
        q = _keywords(question) or {"_"}
        graded = []
        for n in evidence:
            overlap = len(_keywords(n.text) & q) / len(q)
            label = "correct" if overlap >= 0.5 else "ambiguous" if overlap > 0 else "incorrect"
            graded.append(GradedNode(node=n, label=label, score=min(1.0, overlap)))
        return graded

    def refine(self, question: str, evidence: list[EvidenceNode], prev_query: str) -> str:
        # add salient terms from the best evidence we do have
        extra = sorted(_keywords(" ".join(n.text for n in evidence[:3])) - _keywords(prev_query))
        return (prev_query + " " + " ".join(extra[:4])).strip()

    def supported(self, claim: str, evidence: list[EvidenceNode]) -> bool:
        c = _keywords(claim)
        if not c:
            return True
        return any(len(_keywords(n.text) & c) / len(c) >= 0.34 for n in evidence)

    def generate(self, question: str, evidence: list[EvidenceNode]) -> str:
        if not evidence:
            return "未检索到可支撑的证据，无法回答。"
        lines = []
        for n in evidence[: self._top_answer]:
            lines.append(f"[{n.timecode}] {n.text.strip()}")
        return "\n".join(lines)
