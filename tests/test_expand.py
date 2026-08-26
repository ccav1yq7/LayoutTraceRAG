"""Step 3 — HyDE / multi-query expansion in plan; retrieve fuses across queries."""
from layouttrace.config import Config
from layouttrace.graph.nodes import build_nodes
from layouttrace.index import HashEmbedder, InMemoryStore, build_retriever
from layouttrace.llm import HeuristicEngine
from layouttrace.types import EvidenceNode


def _store():
    s = InMemoryStore(HashEmbedder())
    s.add([
        EvidenceNode(id="v:t:1", video_id="v", modality="transcript",
                     text="混合 检索 的 实现 细节", start_s=10, end_s=40),
        EvidenceNode(id="v:t:2", video_id="v", modality="transcript",
                     text="向量 检索 基础", start_s=50, end_s=80),
    ])
    return s


def test_expand_query_multi():
    qs = HeuristicEngine().expand_query("混合 检索 怎么 实现")
    assert len(qs) >= 2
    assert qs[0] == "混合 检索 怎么 实现"


def test_plan_emits_queries():
    nodes = build_nodes(build_retriever(_store()), HeuristicEngine(), Config())
    out = nodes["plan"]({"question": "混合 检索 怎么 实现"})
    assert len(out["queries"]) >= 2
    assert out["query"] == out["queries"][0]


def test_retrieve_fuses_multiple_queries_deduped():
    nodes = build_nodes(build_retriever(_store()), HeuristicEngine(), Config())
    out = nodes["retrieve"]({"queries": ["混合 检索", "向量 检索"], "iteration": 0, "evidence": []})
    ids = [n.id for n in out["evidence"]]
    assert ids and len(ids) == len(set(ids))     # fused, no duplicates
