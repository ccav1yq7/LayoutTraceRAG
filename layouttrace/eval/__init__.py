"""Evaluation: reproducible Recall@k / timecode-localization / faithfulness / latency."""
from __future__ import annotations

from .harness import EvalExample, EvalReport, ExampleResult, evaluate, load_examples
from .metrics import mean, midpoint_in, overlaps, percentile, temporal_iou, token_f1, tokenize
from .video_mme import from_records, synthetic_corpus

__all__ = [
    "EvalExample", "EvalReport", "ExampleResult", "evaluate", "load_examples",
    "temporal_iou", "overlaps", "midpoint_in", "token_f1", "tokenize", "percentile", "mean",
    "from_records", "synthetic_corpus",
]
