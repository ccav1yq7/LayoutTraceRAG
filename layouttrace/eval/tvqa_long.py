"""Loader for the TVQA-Long benchmark (Vision-CAIR/TVQA-Long).

Each TV *episode* is treated as one long video: its ordered clips are
concatenated onto a single timeline, subtitle lines become timestamped
``EvidenceNode``s, and every question's clip-relative gold span ``ts`` is
offset onto that same timeline. This yields real Recall@k / timecode-
localization labels over ~20-min videos — exactly what the harness scores.

Only the two small annotation JSONs are needed (no raw video):
  tvqa-long-annotations/tvqa_val_edited.json         (QA + gold ts + clips)
  tvqa-long-annotations/tvqa_preprocessed_subtitles.json  (timestamped subs)
"""
from __future__ import annotations

import json
from pathlib import Path

from ..types import EvidenceNode
from .harness import EvalExample

# show_name -> subtitle-key prefix (Big Bang Theory clips are stored un-prefixed)
SHOW_PREFIX = {
    "Friends": "friends",
    "Castle": "castle",
    "House M.D.": "house",
    "The Big Bang Theory": "",
    "How I Met You Mother": "met",
    "Grey's Anatomy": "grey",
}
_CLIP_GAP_S = 1.0  # inserted between concatenated clips


def _sub_key(show_name: str, clip: str) -> str:
    # TVQA-Long clip ids already carry the show prefix (e.g. "castle_s03e05_...");
    # Big Bang Theory clips are bare — both match the subtitle keys verbatim.
    return clip


def _window_node(epid: str, clip: str, w: int, offset: float,
                 buf: list[tuple[float, float, str]]) -> EvidenceNode:
    st, en = buf[0][0], max(b[1] for b in buf)
    return EvidenceNode(
        id=f"{epid}:{clip}:{w}", video_id=epid, modality="transcript",
        text=" ".join(b[2] for b in buf), start_s=offset + st, end_s=offset + max(st, en),
    )


def _sliding_windows(lines: list[tuple[float, float, str]], chunk_seconds: float, stride: float,
                     epid: str, clip: str, offset: float) -> list[EvidenceNode]:
    """Overlapping ~chunk_seconds windows advancing every ``stride`` seconds, so a
    span straddling a hard chunk boundary still has a full, un-split window that
    covers it — a standard RAG chunking fix for boundary-cut recall loss."""
    if not lines:
        return []
    end_time = max(en for _, en, _ in lines)
    nodes: list[EvidenceNode] = []
    t, w = 0.0, 0
    while t < end_time:
        buf = [(st, en, tx) for st, en, tx in lines if st < t + chunk_seconds and en > t]
        if buf:
            nodes.append(_window_node(epid, clip, w, offset, buf))
            w += 1
        t += stride
    return nodes


def _parse_ts(ts) -> tuple[float, float] | None:
    try:
        a, b = str(ts).split("-")
        s, e = float(a), float(b)
    except (ValueError, AttributeError):
        return None
    if e < s:
        s, e = e, s
    return (max(0.0, s), max(0.0, e))


class TvqaEpisode:
    """One long video: concatenated evidence nodes + its gold-labelled questions."""

    def __init__(self, episode_id: str, nodes: list[EvidenceNode], examples: list[EvalExample],
                 clips: list[str] | None = None, clip_offset: dict[str, float] | None = None,
                 show_name: str = "") -> None:
        self.episode_id = episode_id
        self.nodes = nodes
        self.examples = examples
        self.clips = clips or []                 # ordered clip ids on the concatenated timeline
        self.clip_offset = clip_offset or {}     # clip id -> start offset (s), to align frames
        self.show_name = show_name


def load_tvqa_long(
    qa_path: str | Path,
    subs_path: str | Path,
    *,
    max_episodes: int | None = None,
    max_questions_per_ep: int | None = None,
    chunk_seconds: float = 30.0,
    chunk_stride: float | None = None,
    shows: set[str] | None = None,
) -> list[TvqaEpisode]:
    subs = {s["vid_name"]: s["sub"] for s in json.loads(Path(subs_path).read_text())}
    qa = json.loads(Path(qa_path).read_text())

    # Gather (show, season, ep) keys grouped by show, then round-robin interleave
    # across shows so a capped sample spans all 6 series rather than one.
    per_show: dict[str, list] = {}
    for show, seasons in qa.items():
        for season, eps in seasons.items():
            for ep, rec in eps.items():
                if (rec.get("questions") and rec.get("clips")):
                    per_show.setdefault(show, []).append((season, ep, rec))
    ordered: list = []
    lists = list(per_show.values())
    for i in range(max(len(v) for v in lists) if lists else 0):
        for v in lists:
            if i < len(v):
                ordered.append(v[i])

    episodes: list[TvqaEpisode] = []
    for season, ep, rec in ordered:
        questions = rec.get("questions") or []
        clips = rec.get("clips") or []
        show_name = questions[0].get("show_name", "")
        if shows and show_name not in shows:
            continue
        prefix = SHOW_PREFIX.get(show_name, "") or "bbt"
        epid = f"{prefix}_{season}_{ep}"

        # concatenate clips onto one timeline
        nodes: list[EvidenceNode] = []
        clip_offset: dict[str, float] = {}
        offset = 0.0
        for clip in clips:
            clip_offset[clip] = offset
            raw = subs.get(_sub_key(show_name, clip)) or []
            lines = [(float(l["start"]), float(l["end"]), (l.get("text") or "").strip())
                     for l in raw if (l.get("text") or "").strip()]
            dur = max((e for _, e, _ in lines), default=0.0)
            stride = chunk_stride if chunk_stride else chunk_seconds
            if stride < chunk_seconds:
                # overlapping sliding windows — avoids splitting an answer across a
                # hard chunk boundary (see _sliding_windows)
                nodes.extend(_sliding_windows(lines, chunk_seconds, stride, epid, clip, offset))
            else:
                # non-overlapping: group consecutive subtitle lines into ~chunk_seconds
                # windows (the way real video-RAG retrieves segments). 0 => per line.
                buf: list[tuple[float, float, str]] = []
                w_start = None
                w = 0
                for st, en, tx in lines:
                    if w_start is None:
                        w_start = st
                    buf.append((st, en, tx))
                    if (not chunk_seconds) or (en - w_start) >= chunk_seconds:
                        nodes.append(_window_node(epid, clip, w, offset, buf))
                        buf, w_start, w = [], None, w + 1
                if buf:
                    nodes.append(_window_node(epid, clip, w, offset, buf))
            offset += dur + _CLIP_GAP_S
        if not nodes:
            continue

        examples: list[EvalExample] = []
        for q in questions:
            clip = q.get("vid_name")
            span = _parse_ts(q.get("ts"))
            if clip not in clip_offset or span is None:
                continue
            off = clip_offset[clip]
            ai = q.get("answer_idx")
            gold_answer = q.get(f"a{ai}") if ai is not None else None
            examples.append(EvalExample(
                id=str(q.get("qid")), question=q["q"], video_id=epid,
                gold_start_s=off + span[0], gold_end_s=off + span[1],
                gold_answer=gold_answer,
            ))
            if max_questions_per_ep and len(examples) >= max_questions_per_ep:
                break
        if examples:
            episodes.append(TvqaEpisode(epid, nodes, examples, clips=clips,
                                        clip_offset=clip_offset, show_name=show_name))
            if max_episodes and len(episodes) >= max_episodes:
                return episodes
    return episodes
