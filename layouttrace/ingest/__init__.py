"""Video ingestion: ASR + keyframes + on-frame OCR -> EvidenceNodes."""
from __future__ import annotations

from .video import extract_keyframes, ingest_video, ocr_frames, transcribe

__all__ = ["ingest_video", "transcribe", "extract_keyframes", "ocr_frames"]
