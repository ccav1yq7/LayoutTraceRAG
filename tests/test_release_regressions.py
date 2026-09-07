"""Public-release review regressions; no semantic-model downloads or provider calls."""
import argparse
import sys
from types import ModuleType
from unittest.mock import patch

import pytest

from layouttrace import cli
from layouttrace.config import Config
from layouttrace.graph import build_graph, answer_question
from layouttrace.graph.nodes import build_nodes
from layouttrace.index import HashEmbedder, InMemoryStore, build_retriever
from layouttrace.index.embed import get_embedder
from layouttrace.llm import HeuristicEngine
from layouttrace.types import EvidenceNode, GradedNode, Citation


def evidence(count=30):
    return [EvidenceNode(id=f"n{i}", video_id="v", modality="transcript", text=f"topic evidence {i}", start_s=i*10, end_s=i*10+5) for i in range(count)]


class Retriever:
    def retrieve(self, query, k):
        return evidence()[:k]


class TargetRanker:
    def rerank(self, query, nodes, k):
        self.received = len(nodes)
        return sorted(nodes, key=lambda n: n.id != "n9")[:k]


def test_rank_ten_candidate_can_enter_final_top_eight():
    ranker = TargetRanker()
    nodes = build_nodes(Retriever(), HeuristicEngine(), Config(top_k=8, candidate_k=30), ranker)
    state = {"question": "topic", "query": "topic", "queries": ["topic"], "evidence": []}
    state.update(nodes["retrieve"](state))
    ranked = nodes["rerank"](state)["evidence"]
    assert ranker.received == 30
    assert ranked[0].id == "n9" and len(ranked) == 8


@pytest.mark.parametrize("operation", ["ask", "eval"])
def test_cli_wires_configured_reranker(operation, tmp_path):
    args = argparse.Namespace(question="q", examples=str(tmp_path/"examples.json"), out=str(tmp_path/"out.json"), iou=0.1)
    (tmp_path/"examples.json").write_text("[]")
    marker = TargetRanker()
    with patch.object(cli, "Config", return_value=Config(use_reranker=True)), patch.object(cli, "LanceStore"), patch.object(cli, "build_retriever"), patch.object(cli, "get_engine"), patch.object(cli, "get_reranker", return_value=marker), patch.object(cli, "build_graph") as build, patch.object(cli, "answer_question"):
        getattr(cli, "_" + operation)(args)
        assert build.call_args.kwargs["reranker"] is marker


def test_corrupt_model_is_an_error_and_hash_is_explicit():
    module = ModuleType("langchain_huggingface")
    def fail(**kwargs):
        raise RuntimeError("corrupt files")
    module.HuggingFaceEmbeddings = fail
    with patch.dict(sys.modules, {"langchain_huggingface": module}):
        with pytest.raises(RuntimeError, match="Cannot load"):
            get_embedder("BAAI/bge-m3")
        assert isinstance(get_embedder("hash-demo"), HashEmbedder)


class Reject(HeuristicEngine):
    def evaluate_evidence(self, question, nodes):
        return [GradedNode(node=n, label="incorrect", score=0) for n in nodes]
    def generate(self, question, nodes):
        raise AssertionError("Rejected evidence must not reach the generator")


def test_all_rejected_evidence_produces_insufficient_answer():
    graph = build_graph(Retriever(), Reject(), Config(max_iterations=0))
    answer = answer_question(graph, "topic")
    assert "证据不足" in answer.text and not answer.citations and not answer.grounded


@pytest.mark.parametrize("text", ["topic evidence is here", "topic evidence [99:00:00-99:00:05]", "[00:00:00-00:00:05]"])
def test_missing_unknown_or_empty_citation_is_not_fabricated(text):
    verify = build_nodes(Retriever(), HeuristicEngine(), Config())["verify"]
    result = verify({"answer": text, "evidence": evidence(1)})
    assert not result["citations"] and not result["grounded"]


def test_claim_checked_against_its_cited_node_not_unrelated_support():
    nodes = evidence(2)
    nodes[1].text = "bananas yellow tropical"
    verify = build_nodes(Retriever(), HeuristicEngine(), Config())["verify"]
    result = verify({"answer": f"bananas yellow tropical [{nodes[0].timecode}]", "evidence": nodes})
    assert not result["grounded"] and not result["citations"]


def test_top_citation_from_wrong_video_is_not_localized():
    from layouttrace.eval.harness import evaluate, EvalExample
    nodes = evidence(2)
    nodes[0].video_id = "wrong"
    class Graph:
        def invoke(self, state):
            return {"citations": [Citation.from_node(n) for n in nodes], "grounded": True}
    report = evaluate(Graph(), [EvalExample(id="q", question="q", video_id="v", gold_start_s=10, gold_end_s=15)])
    assert report.recall_at_k == 1 and report.timecode_localization_acc == 0

