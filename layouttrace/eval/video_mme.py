"""Adapters + a synthetic corpus for the evaluation harness.

``from_records`` maps Video-MME-style question records onto :class:`EvalExample`.
Video-MME ships questions with a temporal-grounding span; point the key names at
your export and the harness does the rest. ``synthetic_corpus`` returns a tiny
self-contained corpus so the harness (and its metrics) can be exercised — and
demoed end-to-end — without downloading anything.
"""
from __future__ import annotations

from ..types import EvidenceNode
from .harness import EvalExample


def from_records(
    records: list[dict],
    *,
    id_key: str = "id",
    question_key: str = "question",
    video_key: str = "video_id",
    start_key: str = "gold_start_s",
    end_key: str = "gold_end_s",
    answer_key: str = "answer",
) -> list[EvalExample]:
    """Map raw benchmark dicts → EvalExample, tolerating differing key names."""
    out: list[EvalExample] = []
    for i, r in enumerate(records):
        out.append(EvalExample(
            id=str(r.get(id_key, i)),
            question=r[question_key],
            video_id=str(r[video_key]),
            gold_start_s=float(r[start_key]),
            gold_end_s=float(r[end_key]),
            gold_answer=r.get(answer_key),
        ))
    return out


def synthetic_corpus() -> tuple[list[EvidenceNode], list[EvalExample]]:
    """A 1-video toy corpus with three gold-annotated questions."""
    vid = "lecture01"
    nodes = [
        EvidenceNode(id=f"{vid}:t:0", video_id=vid, modality="transcript",
                     text="开场 介绍 课程 目标 与 大纲", start_s=0, end_s=60),
        EvidenceNode(id=f"{vid}:t:1", video_id=vid, modality="transcript",
                     text="讲解 稠密 向量 检索 的 基本 原理", start_s=300, end_s=360),
        EvidenceNode(id=f"{vid}:t:2", video_id=vid, modality="transcript",
                     text="重点 讲 混合 检索 把 BM25 与 稠密 向量 用 RRF 融合", start_s=1500, end_s=1560),
        EvidenceNode(id=f"{vid}:t:3", video_id=vid, modality="transcript",
                     text="最后 讨论 重排序 用 cross encoder 提升 精度", start_s=2400, end_s=2460),
        EvidenceNode(id=f"{vid}:f:1", video_id=vid, modality="frame_ocr",
                     text="Hybrid Retrieval = Dense + BM25 (RRF)", start_s=1520, end_s=1520),
    ]
    examples = [
        EvalExample(id="q1", question="混合 检索 是 怎么 融合 的", video_id=vid,
                    gold_start_s=1500, gold_end_s=1560, gold_answer="用 RRF 把 BM25 与 稠密 向量 融合"),
        EvalExample(id="q2", question="重排序 用 的 什么 模型", video_id=vid,
                    gold_start_s=2400, gold_end_s=2460, gold_answer="cross encoder 重排序"),
        EvalExample(id="q3", question="稠密 向量 检索 的 原理", video_id=vid,
                    gold_start_s=300, gold_end_s=360, gold_answer="稠密 向量 检索 原理"),
    ]
    return nodes, examples
