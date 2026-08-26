"""Step 6 — evaluation harness: metric primitives + end-to-end report."""
import json

from layouttrace.config import Config
from layouttrace.eval import (
    evaluate,
    from_records,
    midpoint_in,
    overlaps,
    percentile,
    synthetic_corpus,
    temporal_iou,
    token_f1,
)
from layouttrace.graph import build_graph
from layouttrace.index import HashEmbedder, InMemoryStore, build_retriever
from layouttrace.llm import HeuristicEngine


def test_metric_primitives():
    assert temporal_iou((0, 10), (0, 10)) == 1.0
    assert temporal_iou((0, 10), (20, 30)) == 0.0
    assert temporal_iou((0, 10), (5, 15)) == 5 / 15
    assert token_f1("混合 检索 融合", "混合 检索 融合") == 1.0
    assert token_f1("完全 无关", "混合 检索") == 0.0
    assert percentile([1, 2, 3, 4], 50) == 2.5


def test_point_span_frame_localizes():
    """A frame citation (start == end) inside the gold window must count as a hit."""
    frame, gold = (1520.0, 1520.0), (1500.0, 1560.0)
    assert overlaps(frame, gold) is True          # inclusive, point-aware
    assert midpoint_in(frame, gold) is True        # centre inside gold → localized
    assert overlaps((2000.0, 2000.0), gold) is False


def test_from_records_maps_keys():
    ex = from_records([{"id": "a", "question": "q?", "video_id": "v",
                        "gold_start_s": 10, "gold_end_s": 20, "answer": "ans"}])
    assert ex[0].id == "a" and ex[0].gold_answer == "ans" and ex[0].video_id == "v"


def _graph():
    nodes, examples = synthetic_corpus()
    store = InMemoryStore(HashEmbedder())
    store.add(nodes)
    return build_graph(build_retriever(store, Config()), HeuristicEngine(), Config()), examples


def test_end_to_end_report():
    graph, examples = _graph()
    report = evaluate(graph, examples, iou_threshold=0.1)
    assert report.n == 3
    assert 0.0 <= report.recall_at_k <= 1.0
    assert report.recall_at_k >= 0.6            # heuristic finds the gold spans
    assert 0.0 <= report.faithfulness <= 1.0
    assert report.answer_f1 is not None
    assert report.latency_ms["p95"] >= report.latency_ms["p50"] >= 0.0
    assert len(report.per_example) == 3
    assert "Recall@k" in report.summary()


def test_report_round_trips_json(tmp_path):
    graph, examples = _graph()
    report = evaluate(graph, examples)
    path = tmp_path / "report.json"
    report.save(path)
    loaded = json.loads(path.read_text())
    assert loaded["n"] == 3 and "recall_at_k" in loaded
