"""Step 1 — two-stage retrieval: the rerank node reorders the working set."""
from layouttrace.config import Config
from layouttrace.graph import build_graph
from layouttrace.graph.nodes import build_nodes
from layouttrace.index import HashEmbedder, InMemoryStore, build_retriever
from layouttrace.llm import HeuristicEngine
from layouttrace.retrieval.rerank import IdentityReranker, Reranker
from layouttrace.types import EvidenceNode


class KeywordReranker(Reranker):
    """Fake cross-encoder: rank nodes whose text contains ``want`` first."""

    def __init__(self, want: str) -> None:
        self.want = want

    def rerank(self, query, nodes, k):
        return sorted(nodes, key=lambda n: self.want not in n.text)[:k]


def _nodes():
    return [
        EvidenceNode(id="v:t:1", video_id="v", modality="transcript", text="无关内容 alpha", start_s=0, end_s=5),
        EvidenceNode(id="v:t:2", video_id="v", modality="transcript", text="命中 TARGET 的证据", start_s=10, end_s=15),
    ]


def test_rerank_node_reorders():
    store = InMemoryStore(HashEmbedder()); store.add(_nodes())
    nodes = build_nodes(build_retriever(store), HeuristicEngine(), Config(), reranker=KeywordReranker("TARGET"))
    state = {"question": "q", "query": "q", "queries": ["q"], "evidence": _nodes()}
    out = nodes["rerank"](state)
    assert out["evidence"][0].id == "v:t:2"  # TARGET node promoted to top


def test_rerank_node_added_to_graph_when_enabled():
    store = InMemoryStore(HashEmbedder()); store.add(_nodes())
    with_rr = build_graph(build_retriever(store), HeuristicEngine(), Config(), reranker=IdentityReranker())
    without = build_graph(build_retriever(store), HeuristicEngine(), Config())
    assert "rerank" in set(with_rr.get_graph().nodes)
    assert "rerank" not in set(without.get_graph().nodes)
