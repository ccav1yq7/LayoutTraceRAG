# ADR 0006: M4 Agent, durable sessions and simulated execution

Date: 2026-09-09. Acceptance defined before implementation.

Use LangGraph routing with a separate SQLite checkpointer and an authoritative
business run state. Resumption begins from the latest business checkpoint, while
tool results reconcile through a durable ledger; graph checkpoints alone never
prove a side effect happened. Session revision/client-message uniqueness and a
single active run prevent overlap. Per-run file locks prevent concurrent workers.
Product changes start a new task and remove old working evidence; history remains
archived. Principal and authorized scope are backend state, never planner arguments.

Planner outputs tool/clarify/draft/handoff/abstain decisions. Typed tool argument objects are validated twice, first against the planner
field whitelist and then against each tool schema. Read/search/image observations are untrusted
context. Evidence finalization reuses M3's mandatory checks, with only retrieved
IDs and inspected image IDs; no hidden second retrieval or unmetered finalization.

Shared per-turn limits: 8 planner decisions, 12 tool attempts, 12 model calls,
2 reserved finalization calls, 4 pages, 8 total image inputs, 90 seconds. Every
nested model call is counted and checkpointed before dispatch; provider token
usage is accumulated and checked after responses. Missing usage is not zero cost.
Identical tools with no new evidence stop after two repeats. Cancel flags are
checked at boundaries and after model calls; no completed side effect is rolled back.

Simulated requests only: prepare -> human approval bound to principal/session/task/
revision/argument hash/expiry -> commit. The local ticket insertion has a unique
confirmation key. Response loss is reconciled by querying the ticket; unresolved
external-style effects become OUTCOME_UNKNOWN and are never automatically replayed.
No real order, payment, refund, email or external business system is connected.

Tests cover multi-turn references, product isolation, replay/conflicts, stale
confirmations, exact local ticket deduplication, unknown outcome, cancellation,
budget reserves, no progress, failed verification repair and interrupted nodes.
B2/B3 share source/model/primitives and outer budgets in engineering comparisons;
formal effect claims still require completed live benchmark setup.

Live validation revision: the first experiment requested a JSON string for tool
arguments, but DeepSeek returned an object. The negative trace is retained. Agent
prompt v2 uses an explicit typed argument object and requires JSON observations.
Read failures are terminal ledger entries, preventing accidental repeated model
calls from an uncaught parsing error. Unknown usage is reported as unknown.

B2_shared_tools is a new fixed search/read/inspect/finalize control using exactly
the Agent tool executor, ledger and outer budget. It is labeled separately from
M3 B2; its measurements are not merged into historical B2 tables. The live sample
and follow-up are engineering probes with hash/RRF retrieval, not benchmark scores.
