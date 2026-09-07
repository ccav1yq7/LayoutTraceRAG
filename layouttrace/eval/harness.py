"""End-to-end evaluation harness.

Runs the compiled agentic graph over a labelled set and reports the numbers that
matter for evidence-traceable long-video QA:

- **Recall@k** — any cited span overlaps the gold answer span (right video).
- **Timecode-localization accuracy** — the *top* citation's temporal IoU with the
  gold span clears ``iou_threshold`` (did we point the player at the right moment?).
- **Faithfulness** — share of answers the Self-RAG ``verify`` node judged grounded.
- **Answer F1** — token-overlap F1 vs a gold answer, when one is provided.
- **Latency** — per-query wall time, reported as mean / p50 / p95.

The report is JSON-serialisable → drop it in a repo as reproducible evidence.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel, Field

from .metrics import mean, midpoint_in, overlaps, percentile, temporal_iou, token_f1


class EvalExample(BaseModel):
    id: str
    question: str
    video_id: str
    gold_start_s: float = Field(ge=0)
    gold_end_s: float = Field(ge=0)
    gold_answer: str | None = None


class ExampleResult(BaseModel):
    id: str
    recall_hit: bool
    localized: bool
    grounded: bool
    answer_f1: float | None
    latency_ms: float
    n_citations: int


class EvalReport(BaseModel):
    n: int
    recall_at_k: float
    timecode_localization_acc: float
    faithfulness: float
    answer_f1: float | None
    latency_ms: dict[str, float]
    config: dict = Field(default_factory=dict)
    per_example: list[ExampleResult] = Field(default_factory=list)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.model_dump(), ensure_ascii=False, indent=2))

    def summary(self) -> str:
        f1 = "n/a" if self.answer_f1 is None else f"{self.answer_f1:.3f}"
        return (
            f"n={self.n}  Recall@k={self.recall_at_k:.3f}  "
            f"Localize={self.timecode_localization_acc:.3f}  "
            f"Faithful={self.faithfulness:.3f}  AnswerF1={f1}  "
            f"P95={self.latency_ms['p95']:.0f}ms"
        )


def _now() -> float:
    return time.perf_counter()


def evaluate(graph, examples: list[EvalExample], *, iou_threshold: float = 0.1) -> EvalReport:
    """Run ``graph`` over ``examples`` and aggregate the metrics."""
    results: list[ExampleResult] = []
    f1s: list[float] = []

    for ex in examples:
        gold = (ex.gold_start_s, ex.gold_end_s)
        t0 = _now()
        final = graph.invoke({"question": ex.question})
        latency_ms = (_now() - t0) * 1000.0

        cites = [c for c in final.get("citations", []) if c.video_id == ex.video_id]
        recall_hit = any(overlaps((c.start_s, c.end_s), gold) for c in cites)
        # localized: top citation's IoU clears the bar OR its centre lands inside the
        # gold window — the latter credits instantaneous frame citations (start == end).
        all_cites = final.get("citations", [])
        first = all_cites[0] if all_cites else None
        top = (first.start_s, first.end_s) if first else None
        localized = first is not None and first.video_id == ex.video_id and (
            temporal_iou(top, gold) >= iou_threshold or midpoint_in(top, gold)
        )
        grounded = bool(final.get("grounded", False))

        f1 = None
        if ex.gold_answer is not None:
            f1 = token_f1(final.get("answer", ""), ex.gold_answer)
            f1s.append(f1)

        results.append(ExampleResult(
            id=ex.id, recall_hit=recall_hit, localized=localized, grounded=grounded,
            answer_f1=f1, latency_ms=latency_ms, n_citations=len(cites),
        ))

    lat = [r.latency_ms for r in results]
    return EvalReport(
        n=len(results),
        recall_at_k=mean([1.0 if r.recall_hit else 0.0 for r in results]),
        timecode_localization_acc=mean([1.0 if r.localized else 0.0 for r in results]),
        faithfulness=mean([1.0 if r.grounded else 0.0 for r in results]),
        answer_f1=(mean(f1s) if f1s else None),
        latency_ms={"mean": mean(lat), "p50": percentile(lat, 50), "p95": percentile(lat, 95)},
        config={"iou_threshold": iou_threshold},
        per_example=results,
    )


def load_examples(path: str | Path) -> list[EvalExample]:
    """Load a JSON list of examples (Video-MME-style) → ``EvalExample`` objects."""
    raw = json.loads(Path(path).read_text())
    return [EvalExample(**e) for e in raw]
