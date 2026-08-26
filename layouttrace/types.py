"""Core data contracts for evidence-traceable long-video RAG.

Every retrievable unit is an :class:`EvidenceNode` carrying a time span, so any
answer can be traced back to a player-ready timecode.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Modality = Literal["transcript", "frame_ocr", "frame", "caption", "event"]
Relevance = Literal["correct", "ambiguous", "incorrect"]


def format_timecode(start_s: float, end_s: float | None = None) -> str:
    """``150.0 -> '00:02:30'`` ; with ``end`` -> ``'00:02:30-00:02:48'``."""

    def hms(t: float) -> str:
        t = max(0, int(round(t)))
        return f"{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}"

    return hms(start_s) if end_s is None else f"{hms(start_s)}-{hms(end_s)}"


class EvidenceNode(BaseModel):
    """One timestamped, modality-tagged piece of retrievable evidence."""

    id: str
    video_id: str
    modality: Modality
    text: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    meta: dict = Field(default_factory=dict)

    @property
    def timecode(self) -> str:
        return format_timecode(self.start_s, self.end_s)


class GradedNode(BaseModel):
    """A retrieved node with its CRAG relevance judgement."""

    node: EvidenceNode
    label: Relevance
    score: float = Field(ge=0, le=1)


class Citation(BaseModel):
    """A grounded reference attached to a generated answer."""

    video_id: str
    node_id: str
    start_s: float
    end_s: float
    snippet: str

    @property
    def timecode(self) -> str:
        return format_timecode(self.start_s, self.end_s)

    @classmethod
    def from_node(cls, node: EvidenceNode, max_chars: int = 160) -> "Citation":
        snippet = node.text.strip().replace("\n", " ")
        if len(snippet) > max_chars:
            snippet = snippet[: max_chars - 1] + "…"
        return cls(
            video_id=node.video_id,
            node_id=node.id,
            start_s=node.start_s,
            end_s=node.end_s,
            snippet=snippet,
        )


class Answer(BaseModel):
    """Final grounded answer with the evidence it cites."""

    question: str
    text: str
    citations: list[Citation] = Field(default_factory=list)
    grounded: bool = True
    iterations: int = 1

    def render(self) -> str:
        lines = [self.text.rstrip()]
        if self.citations:
            lines.append("\n来源:")
            for c in self.citations:
                lines.append(f"  [{c.timecode}] {c.snippet}")
        return "\n".join(lines)
