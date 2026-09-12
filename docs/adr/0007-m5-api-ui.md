# ADR 0007: M5 same-origin API and working UI

Date: 2026-09-09. Acceptance defined before implementation.

Preserve the PLAN stack: FastAPI/Uvicorn, existing SQLite/LanceDB/Agent and React +
TypeScript + Vite. Sites skills guide working-surface design, accessibility and QA.
Their Cloudflare Worker hosting runtime cannot run this Python/native backend;
do not replace the approved architecture or publish a disconnected UI. M5 is local
integration and browser acceptance; deployment remains M7's Compose work.

Use same-origin HTTP-only auth cookies plus CSRF, or explicit backend-issued bearer
tokens. Local demo bootstrap is loopback-only with trusted-host/origin checks;
non-demo mode has no anonymous bootstrap. Never trust client principal IDs or
forwarded identity headers. Authorize every session, run, event, source and upload.
Completed answers are revalidated on delivery; source revocation withholds stale
instructions/images. Responses and media are no-store.

A bounded thread worker executes durable runs outside the event loop. Startup
recovers queued/running records; SSE replays sequenced events and never starts runs.
Expose only safe progress payloads, not prompts, observations or unchecked drafts.

Uploads are byte/pixel/MIME bounded, decoded then normalized to metadata-free PNG.
Store original-byte hash and normalized-byte hash; discard original bytes. Bind
uploads to principal/session/task, expire by default after 24 hours and keep them
out of public/manual indexes. Inspection is a separately metered image tool, not
authoritative manual evidence. Messages bind attachment IDs and step-reply anchors
to idempotency keys. Human confirmations retain M4's exact binding and ledger.

UI: navy/blue working surface, product rail, readable chat/step cards, source dialog,
image upload, progress, cancel, retry/recovery, service confirmation and feedback.
Use only source/test-manual images; no decorative or generated product imagery.
Tests cover actual HTTP ownership/CSRF/media, SSE replay, uploads, mobile/keyboard,
product switching, multi-turn, confirmations and browser reload recovery.
