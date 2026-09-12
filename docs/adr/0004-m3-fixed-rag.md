# ADR 0004: M3 fixed multimodal RAG and evaluation boundary

Date: 2026-09-09. Accepted for implementation; live benchmark acceptance pending.

Acceptance before edits: B1 sends text only; B2 passes authorized image bytes to
selection, writing and independent verification. Writers cannot set scope, source
locators or verification results. Every output statement has an evidence reference;
all displayed assets were inspected and belong to cited evidence. L1 schema and
L2 grant/version/variant checks cannot be approved by a model. L3 must cover every
claim and step/image pair. Failure produces abstention, never unsupported text.
Missing images may produce a labeled text-only partial answer; hash corruption, revoked or
foreign evidence remains forbidden. No model tools, arbitrary URLs or reasoning
logs. Fixed request/image/page budgets bound the pipeline.

The existing renderer keeps its safe not_checked default. Only a successful
independent verification in the fixed pipeline can add the verified status; fake
verification remains not_checked in public output. Fake extractive fixtures are
explicit and never become official effect results. A Responses gateway will be
implemented and tested with local transport fixtures, without calling the known
failing service. All LLM roles remain gpt-5.6-terra.

Prediction requests are strict whitelists: current question, manual, protocol and
only for given-page the permitted known page. Evaluation-only export/scoring modules
read eval_private; the predictor never imports them or opens gold files. Preserve
all scheduled questions, including missing predictions and failures. Validate IDs,
manual membership, visited pages and actually cited regions before scoring.

Profiles are pm209-given-page, pm209-retrieved-top1 and pm209-multipage. The neutral
multipage name is deliberate: this round implements fixed B1/B2, not M4's Agent.
Given-page information and extended multipage semantics are always explicit.
Official-compatible region metrics and strict extensions are separate. Full
NLGEval scoring, 50-query real val smoke and fixed real effect baselines remain
external acceptance gates; no synthetic numbers will be reported as those results.
