# ADR 0002: User-supplied model capability profile

Date: 2026-09-09. Status: accepted configuration, capability verification pending.

The user supplied `LLM.comfig`, then explicitly corrected all LLM roles to
`gpt-5.6-terra`. Both model and review_model fields now name that model. Use this
model for the current planner, vision and verifier profile; do not silently fall
back to Qwen or another model. Retrieval embedding/reranker choices are separate.
The original Qwen choices in the plan are historical proposals, not validated
models for this run.

The supplied file combines TOML-like service configuration with a JSON API-key
entry. It is treated as a private local file and ignored by Git. The bounded smoke
script extracts only the required values in memory, sends two synthetic requests,
sets store=false, uses a 45-second timeout and 300 output tokens per request, and
stores no credentials, endpoint URL, response reasoning or raw error body.
The exact configured base URL is used without inventing a /v1 suffix.

Request formats follow the official [structured-output guide](https://developers.openai.com/api/docs/guides/structured-outputs)
and [image-input guide](https://developers.openai.com/api/docs/guides/images-vision).
These document request formats, not the availability or identity of the user's
provider deployment. Runtime model identity, immutable revision and tariff remain
unverified unless separately observed. Responses capability probes do not establish
ECom adapter compatibility or official benchmark performance.
