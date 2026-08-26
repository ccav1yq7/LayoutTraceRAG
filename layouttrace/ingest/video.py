"""Long-video ingestion → timestamped :class:`EvidenceNode`s.

Pipeline: faster-whisper ASR (temporally chunked transcript nodes) + PyAV
scene-change keyframes with RapidOCR on-frame text. Heavy deps are imported
lazily and only when this runs, so importing the package stays cheap.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import Config
from ..types import EvidenceNode


def _nid(video_id: str, modality: str, start_s: float, text: str) -> str:
    h = hashlib.md5(f"{modality}|{start_s:.2f}|{text}".encode()).hexdigest()[:10]
    return f"{video_id}:{modality}:{h}"


def transcribe(path: str, config: Config) -> list[tuple[float, float, str]]:
    """Return ``(start_s, end_s, text)`` segments via faster-whisper."""
    from faster_whisper import WhisperModel

    model = WhisperModel(config.asr_model, device="auto", compute_type="int8")
    segments, _info = model.transcribe(path, language=config.asr_language, vad_filter=True)
    return [(seg.start, seg.end, seg.text.strip()) for seg in segments if seg.text.strip()]


def _chunk_transcript(
    video_id: str, segments: list[tuple[float, float, str]], chunk_seconds: float
) -> list[EvidenceNode]:
    nodes: list[EvidenceNode] = []
    buf: list[str] = []
    start = end = None
    for s, e, text in segments:
        if start is None:
            start = s
        buf.append(text)
        end = e
        if end - start >= chunk_seconds:
            joined = " ".join(buf)
            nodes.append(EvidenceNode(id=_nid(video_id, "transcript", start, joined),
                                      video_id=video_id, modality="transcript",
                                      text=joined, start_s=start, end_s=end))
            buf, start, end = [], None, None
    if buf and start is not None:
        joined = " ".join(buf)
        nodes.append(EvidenceNode(id=_nid(video_id, "transcript", start, joined),
                                  video_id=video_id, modality="transcript",
                                  text=joined, start_s=start, end_s=end or start))
    return nodes


def extract_keyframes(path: str, config: Config):
    """Yield ``(timestamp_s, PIL.Image)`` at scene changes via PyAV."""
    import av
    import numpy as np
    from PIL import Image

    container = av.open(path)
    stream = container.streams.video[0]
    prev = None
    for frame in container.decode(stream):
        img = frame.to_ndarray(format="rgb24")
        small = img[::16, ::16].astype("float32") / 255.0
        if prev is None or float(np.abs(small - prev).mean()) >= config.scene_threshold:
            ts = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
            yield ts, Image.fromarray(img)
            prev = small
    container.close()


def ocr_frames(video_id: str, frames) -> list[EvidenceNode]:
    from rapidocr import RapidOCR

    ocr = RapidOCR()
    nodes: list[EvidenceNode] = []
    for ts, image in frames:
        import numpy as np

        result = ocr(np.array(image))
        text = " ".join(t for t in (result.txts or []) if t).strip() if result else ""
        if text:
            nodes.append(EvidenceNode(id=_nid(video_id, "frame_ocr", ts, text),
                                      video_id=video_id, modality="frame_ocr",
                                      text=text, start_s=ts, end_s=ts))
    return nodes


def ingest_video(
    path: str, config: Config | None = None, *, with_frames: bool = True
) -> list[EvidenceNode]:
    """End-to-end: transcribe + (optionally) OCR keyframes into evidence nodes."""
    config = config or Config()
    video_id = Path(path).stem
    nodes = _chunk_transcript(video_id, transcribe(path, config), config.chunk_seconds)
    if with_frames:
        nodes += ocr_frames(video_id, extract_keyframes(path, config))
    return nodes
