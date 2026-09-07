"""Step 4 — Self-RAG groundedness: claim attribution + bounded regenerate."""
from layouttrace.config import Config
from layouttrace.graph.nodes import build_nodes, route_after_verify
from layouttrace.index import HashEmbedder, InMemoryStore, build_retriever
from layouttrace.llm import HeuristicEngine
from layouttrace.types import EvidenceNode

EV = [EvidenceNode(id="v:t:1", video_id="v", modality="transcript",
                   text="混合 检索 的 实现 细节", start_s=10, end_s=40)]


def _verify():
    return build_nodes(build_retriever(InMemoryStore(HashEmbedder())), HeuristicEngine(), Config())["verify"]


def test_supported():
    e = HeuristicEngine()
    assert e.supported("混合 检索 实现", EV) is True
    assert e.supported("完全 无关 内容 主题", EV) is False


def test_verify_grounded_answer():
    out = _verify()({"answer": f"混合 检索 的 实现 是 这样 [{EV[0].timecode}]。", "evidence": EV, "regen": 0})
    assert out["grounded"] is True
    assert out["regen"] == 0
    assert out["citations"]


def test_verify_ungrounded_triggers_regen():
    out = _verify()({"answer": "这是 完全 无关 的 内容。", "evidence": EV, "regen": 0})
    assert out["grounded"] is False
    assert out["regen"] == 1
    # router sends it back to regenerate while budget remains, then stops
    assert route_after_verify(Config())(out) == "generate"
    assert route_after_verify(Config())({**out, "regen": 2}) == "end"
