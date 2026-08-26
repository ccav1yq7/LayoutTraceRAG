"""Metric primitives for the evaluation harness (no heavy deps)."""
from __future__ import annotations

import re

_TOK = re.compile(r"[a-z0-9]+|[一-鿿]")


def tokenize(text: str) -> list[str]:
    """Lowercase word/number tokens + per-character CJK — enough for overlap F1."""
    return _TOK.findall(text.lower())


def temporal_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Temporal Intersection-over-Union of two ``(start, end)`` spans."""
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Inclusive overlap. Point spans (frames, ``start == end``) count when they
    fall on/inside the other span, so a frame citation at the right instant hits."""
    return min(a[1], b[1]) >= max(a[0], b[0])


def midpoint_in(span: tuple[float, float], gold: tuple[float, float]) -> bool:
    """Does the span's centre fall inside ``gold``? (point-span aware localization)"""
    mid = (span[0] + span[1]) / 2.0
    return gold[0] <= mid <= gold[1]


def token_f1(pred: str, gold: str) -> float:
    """Set-overlap F1 between predicted and gold answer tokens."""
    p, g = set(tokenize(pred)), set(tokenize(gold))
    if not p or not g:
        return 0.0
    tp = len(p & g)
    if tp == 0:
        return 0.0
    precision, recall = tp / len(p), tp / len(g)
    return 2 * precision * recall / (precision + recall)


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile; ``q`` in [0, 100]. Empty → 0.0."""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (q / 100.0) * (len(xs) - 1)
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
