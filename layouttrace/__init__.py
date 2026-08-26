"""LayoutTraceRAG — evidence-traceable long-video question answering.

An agentic RAG orchestrated with LangGraph: plan → hybrid retrieve → grade →
(refine loop) → generate → verify, where every answer cites player-ready
timecodes.
"""
from __future__ import annotations

from .config import Config
from .types import Answer, Citation, EvidenceNode, format_timecode

__version__ = "0.1.0"
__all__ = ["Config", "Answer", "Citation", "EvidenceNode", "format_timecode", "__version__"]
