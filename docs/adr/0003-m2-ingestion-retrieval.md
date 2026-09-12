# ADR 0003: M2 data, snapshots and scoped retrieval

Date: 2026-09-09. Status: accepted for implementation.

Acceptance written before implementation: full PM209 conversion uses an explicit
corpus whitelist; QA, official IDs and gold stay in eval_private, preserving source
row/question/region order. JSON records and asset paths are validated on ingest.
Page images and PDF renders retain source bytes, hashes, normalized coordinates and
original boxes; derived crop pixels must match their source. Record nearby text
relations as layout heuristics, not semantic truth.

Add sg0002 snapshot tables. STAGING -> AUDITING -> READY -> ACTIVE; failure never
changes the active pointer. Old ACTIVE becomes READY and stays readable for pinned
runs. Compare-and-swap activation detects competing publishers; failed builds can
restart with the same immutable identity. Asset reads and searches reject incomplete
snapshots. Unregistered M1 fixture snapshots retain compatibility, but M2 indexes
always require registered, audited snapshots.

Each immutable generation has its own LanceDB directory. Persist model identity,
revision, dimension, preprocessing, schema and all scope columns. Both exact dense
search and native FTS prefilter by domain, principal, product, variant, document,
language and snapshot before top-k. SQLite remains authoritative on every read;
revoked docs are denied even for cached/pinned results. Fusion uses RRF followed by
an explicit reranker; no implicit fake fallback for a real embedding configuration.

The offline HashEmbedder and RRF-only reranker are labeled fake and verify engineering
only. BGE adapters can be configured with pinned revisions; real weights/inference
validation remain separate from the unavailable LLM service. No model API requests
are needed for M2 acceptance. Tests cover I-01–06, U-07–09, S-01/02/05/07/09 and
corpus/gold isolation. Existing legacy source is not modified.

For PM209 page-image sources, each source page has an immutable DocumentVersion;
the opaque manual product identity groups those versions. This avoids pretending
that the archive provides a PDF or one original byte stream per manual. PDF sources
use one DocumentVersion across pages. Runtime source_reference and region IDs link
back to the source-only corpus; official mapping remains in eval_private.

PDF extraction currently supplies the visible whole-page text region and exact
raster, not automatic figure detection or OCR. FTS uses the locked native tokenizer;
full semantic BGE and multilingual quality remain separate effect tests. READY is
readable only after a successful integrity audit; a crashed READY publication can
resume through publish with the expected active pointer. CLI single-writer locking
prevents simultaneous imports from changing the same metadata or index generation.
