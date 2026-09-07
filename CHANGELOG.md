# 0.2.0 — 2026-09-07

- Fail explicitly when an embedding, reranking, visual model or configured LLM cannot initialize.
- Connect the CLI reranking option and share candidate fusion with the TVQA benchmark; keep the candidate pool until reranking.
- Discard all-incorrect evidence, refuse unsupported answers and never manufacture citations from uncited candidates.
- Verify each cited claim against its cited nodes; reject ambiguous timecodes and mark unverified rendered answers.
- Persist embedding identity, reject legacy/mismatched indexes without overwriting them, upsert node IDs and handle empty stores.
- Use the actual first citation for time-localization scoring, even if it points to the wrong video.
- Correct installation extras and mark previous metrics as historical, not current-release scores.

Migration: rebuild old indexes into a new `LT_TABLE`; keep the original table until the new index is verified.
For offline demos, choose `LT_EMBED_MODEL=hash-demo` or inject `HashEmbedder()` explicitly.

