# LayoutTraceRAG

**Evidence-traceable long-video question answering — an agentic RAG orchestrated with LangGraph.**

Ask a question about a long lecture / meeting / tutorial and get an answer that points back to a **player-ready timecode** (e.g. `00:42:10-00:42:48`). Speech, on-frame text and scene keyframes are ingested as **timestamped evidence nodes**; a LangGraph agent retrieves them with hybrid search, keeps refining when the evidence is thin, and only then answers — with citations.

> LangGraph · LangChain · BGE-M3 · bge-reranker-v2-m3 · CLIP/SigLIP · LanceDB (hybrid vector + full-text) · faster-whisper · PyAV · RapidOCR. **Project code: MIT. Model/data terms are separate; optional LLM services may be paid.**

## Why

Long videos are hard to search: you can't skim them, positional retrieval is hard, and an LLM answer with no source is unverifiable. LayoutTraceRAG makes every answer **grounded and jump-to-able**.

## Architecture

A LangGraph `StateGraph` with an iterative retrieval loop and a groundedness self-check:

```
plan → retrieve → rerank → grade ─┬─ (sufficient | budget) → generate → verify ─┬→ END
                                   └─ (insufficient) ──────→ refine → retrieve ⟲  └→ generate ⟲
```

- **plan** — **HyDE + multi-query**: rewrite the question into several retrieval queries (incl. a hypothetical answer).
- **retrieve** — hybrid **BGE-M3 dense + BM25** (+ optional **cross-modal visual** frame search), fused **per-query and across queries** with **Reciprocal Rank Fusion**; evidence accumulates across rounds, de-duped.
- **rerank** — two-stage retrieval: a **cross-encoder** (`bge-reranker-v2-m3`) reorders the candidate set.
- **grade** — **CRAG**: per-document relevance (correct / ambiguous / incorrect), drop the incorrect, knowledge-refine the rest, and decide sufficiency or trigger a corrective re-search.
- **refine → retrieve** — on a gap, expand the query and search again (bounded loop).
- **generate** — answer strictly from evidence, citing `[HH:MM:SS-HH:MM:SS]`.
- **verify** — **Self-RAG**: split the answer into claims, score claim-level groundedness against the cited evidence, and **regenerate (bounded)** when it falls short.

Every node appends to an accumulated `notes` trace, so a full run is observable end-to-end. Without a key, generation uses an explicitly reported heuristic engine. For model-free embeddings, select `LT_EMBED_MODEL=hash-demo` (not semantic retrieval); swap the store (LanceDB / in-memory) without touching the graph.

## Multimodal retrieval

The persistent CLI currently indexes ASR and frame OCR text. CLIP/SigLIP visual search is exposed through the library and benchmark scripts; persistent visual indexing is not yet wired into the CLI.

Frames are indexed two ways: **on-frame OCR text** joins the lexical/dense side, and a **cross-modal encoder (CLIP / SigLIP)** embeds the frame image so a text question can retrieve frames by their **visual content** — a diagram with no readable text is still findable. The visual ranking joins the RRF fusion with a tunable `visual_weight`. (ColPali-style late-interaction is a drop-in behind the same `VisionEmbedder`.)

## Evaluation

A reproducible harness (`layouttrace eval` / `examples/eval_demo.py`) reports the numbers that matter for traceable video QA:

- **Recall@k** — a cited span overlaps the gold answer span
- **Timecode-localization accuracy** — the top citation's **temporal IoU** with the gold span clears a threshold
- **Faithfulness** — share of answers the Self-RAG `verify` node judged grounded
- **Answer F1** — token overlap vs a gold answer
- **Latency** — mean / p50 / **p95**

```bash
python examples/eval_demo.py                 # runs on a built-in synthetic corpus → report.json
layouttrace eval examples.json --out report.json   # your indexed videos + labelled questions
```

## Real benchmark: TVQA-Long

**Historical measurements (before the September 2026 correctness fixes), not scores for the current release.** The graph previously truncated candidates before reranking; the current graph and benchmark share candidate fusion and require a fresh evaluation.

Ran the experimental pipeline against [TVQA-Long](https://huggingface.co/datasets/Vision-CAIR/TVQA-Long)
(episodes from 6 shows, ~20-minute concatenated timelines, official subtitles + gold
timestamp spans) — real **BGE-M3 + bge-reranker-v2-m3** on an RTX 4090, 18 episodes /
336 questions, all reports in [`results/`](results/):

| Metric | Value |
|---|---|
| Recall@8 | **0.84** |
| Recall@20 | **0.94** |
| Recall@30 | 0.98 |
| Timecode-localization | 0.35–0.41 (segment-size dependent) |
| Retrieval P95 latency | ~0.5s (GPU) |

**We chased Recall@8 to 90%+ and it doesn't move — four independent, cross-validated
levers all converge on the same ~0.84 ceiling** (see
[`results/tvqa_long_recall_ceiling_sweep.json`](results/tvqa_long_recall_ceiling_sweep.json)):
segment granularity (30s→180s), candidate-pool size, sliding-window overlap, and even
swapping the heuristic query expansion for a **real LLM (gpt-5.4)** — none of it helps.
These experiments suggest a representation bottleneck under the tested settings; they do not establish an intrinsic or unique BGE-M3 ceiling.

Multimodal ablation (CLIP frame retrieval vs subtitle-only, `results/tvqa_long_multimodal.json`):
fixing frame retrieval genuinely lifts recall (fusion beats text-only once CLIP works), a
CLIP-friendly query rewrite made things *worse* (a validated negative result), and a
CRAG-style on-demand visual trigger cuts visual-retrieval calls ~47% at near-zero recall
cost. Along the way we root-caused and fixed a real infra bug: a flaky download had
silently dropped CLIP's tokenizer vocab, making it encode every input to a near-constant
vector (not an error — just silently wrong retrieval) until traced to raw `input_ids`.

Honest scope: subtitle-only for the main sweep (dialogue-heavy TV, not the visual-heavy
lecture/slide content this project targets — see Multimodal retrieval above); heuristic
extractive generation unless an LLM key is set.

## Install

```bash
uv sync --locked --extra retrieval --extra video --extra llm
# For offline unit tests only: uv sync --locked --extra dev
```

## Usage

```bash
export OPENAI_API_KEY=...                 # or ANTHROPIC_API_KEY (optional; falls back offline)
uv run --no-sync layouttrace index lecture.mp4             # ASR + keyframe OCR → LanceDB
LT_USE_RERANKER=1 uv run --no-sync layouttrace ask "混合检索是怎么实现的？"    # answer with jump-to timecodes
```

Library:

```python
from layouttrace import Config
from layouttrace.index import HashEmbedder, InMemoryStore, build_retriever
from layouttrace.graph import build_graph, answer_question
from layouttrace.llm import get_engine

store = InMemoryStore(HashEmbedder()); store.add(nodes)        # your EvidenceNodes
graph = build_graph(build_retriever(store), get_engine())
print(answer_question(graph, "…").render())
```

## Runtime and index behavior

Model initialization failures raise errors; no model silently changes to hashing or identity reranking. Model-free tests inject hash/heuristic components explicitly. Existing indexes without embedding identity metadata must be rebuilt into a new `LT_TABLE`; the old index is retained. Unverified answers are marked, and sources are never fabricated from uncited candidates.

With an LLM key configured, questions and retrieved evidence text are sent to that provider. Without a key, the heuristic generator runs locally; semantic/ASR/OCR models may still require initial downloads. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Data and model licenses

This repository contains no videos, datasets, model weights, indexes, or API credentials. Download TVQA-Long, How2QA, Hugging Face models, and any other third-party assets yourself and comply with their respective terms. The included benchmark reports are derived metrics and configurations, not redistributed dataset content.

## Development

```bash
uv sync --extra dev
uv run pytest        # graph + retrieval unit tests (no models/keys needed)
```

## License

MIT — see [LICENSE](LICENSE). Third-party libraries, models and datasets retain their own licenses. This repository does not redistribute model weights or video assets.
