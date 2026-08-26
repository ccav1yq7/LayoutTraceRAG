"""Step 2 — CRAG: per-doc relevance + knowledge refinement + corrective grade."""
from layouttrace.config import Config
from layouttrace.graph.nodes import build_nodes
from layouttrace.index import HashEmbedder, InMemoryStore, build_retriever
from layouttrace.llm import HeuristicEngine
from layouttrace.types import EvidenceNode

RELEVANT = EvidenceNode(id="v:t:1", video_id="v", modality="transcript",
                        text="这里讲 混合 检索 的 实现", start_s=10, end_s=40)
IRRELEVANT = EvidenceNode(id="v:t:2", video_id="v", modality="transcript",
                          text="无关 内容 alpha beta", start_s=0, end_s=5)


def test_evaluate_evidence_labels():
    graded = HeuristicEngine().evaluate_evidence("混合 检索 实现", [RELEVANT, IRRELEVANT])
    by_id = {g.node.id: g.label for g in graded}
    assert by_id["v:t:1"] == "correct"
    assert by_id["v:t:2"] == "incorrect"


def test_grade_node_refines_and_decides():
    store = InMemoryStore(HashEmbedder())
    nodes = build_nodes(build_retriever(store), HeuristicEngine(), Config())
    out = nodes["grade"]({"question": "混合 检索 实现", "evidence": [RELEVANT, IRRELEVANT]})
    kept_ids = {n.id for n in out["evidence"]}
    assert kept_ids == {"v:t:1"}          # irrelevant node dropped (knowledge refinement)
    assert out["sufficient"] is True      # a "correct" doc exists → sufficient


def test_grade_corrective_when_all_irrelevant():
    store = InMemoryStore(HashEmbedder())
    nodes = build_nodes(build_retriever(store), HeuristicEngine(), Config())
    out = nodes["grade"]({"question": "完全 不同 主题", "evidence": [IRRELEVANT]})
    assert out["sufficient"] is False     # no correct doc → route to corrective refine
