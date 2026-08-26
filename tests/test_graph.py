"""End-to-end smoke test of the LangGraph pipeline with an in-memory store and
the no-LLM heuristic engine (no models, no API keys, no network)."""
from layouttrace.config import Config
from layouttrace.graph import answer_question, build_graph
from layouttrace.index import HashEmbedder, InMemoryStore, build_retriever
from layouttrace.llm import HeuristicEngine
from layouttrace.types import EvidenceNode


def _store() -> InMemoryStore:
    nodes = [
        EvidenceNode(id="lec:transcript:1", video_id="lec", modality="transcript",
                     text="今天先介绍向量检索的基础", start_s=10, end_s=40),
        EvidenceNode(id="lec:transcript:2", video_id="lec", modality="transcript",
                     text="接下来讲混合检索 混合 BM25 与向量 的实现", start_s=2530, end_s=2568),
        EvidenceNode(id="lec:frame_ocr:1", video_id="lec", modality="frame_ocr",
                     text="Hybrid Retrieval = Dense + BM25", start_s=2531, end_s=2531),
        EvidenceNode(id="lec:transcript:3", video_id="lec", modality="transcript",
                     text="最后是评测与部署", start_s=3600, end_s=3660),
    ]
    s = InMemoryStore(HashEmbedder())
    s.add(nodes)
    return s


def test_pipeline_answers_with_citations():
    config = Config()
    graph = build_graph(build_retriever(_store(), config), HeuristicEngine(), config)
    ans = answer_question(graph, "混合检索是怎么实现的？")

    assert ans.text.strip()
    assert ans.citations, "answer must be grounded in cited evidence"
    # every citation is a valid player-ready timecode span
    for c in ans.citations:
        assert ":" in c.timecode and "-" in c.timecode
    assert ans.iterations >= 1


def test_graph_has_expected_nodes():
    graph = build_graph(build_retriever(_store()), HeuristicEngine())
    names = set(graph.get_graph().nodes)
    for expected in {"plan", "retrieve", "grade", "refine", "generate", "verify"}:
        assert expected in names
