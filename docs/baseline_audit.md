# ShopGuide M0 baseline audit

Date: 2026-09-09. Scope: current local workspace, before ShopGuide implementation.

The Git repository root is the parent CV directory, not a standalone ShopGuide
checkout. HEAD is `db63244c6cb99b99605c15ea1a79276246c62887`; PLAN's reviewed upstream
SHA `fe4050d1c228c0dd4ecd5d37c4f77b2b9dd395d2` is not the current local HEAD.
The workspace was already dirty. Created branch `shopguide/m0-m1-20260909` without
resetting, reverting, stashing or committing existing changes. Project source file
SHA256s, project-only status and the original lockfile were saved in
`artifacts/shopguide/m0/` before implementation. This is a file-hash audit, not a
claim that the dirty workspace equals an upstream commit.

Baseline environment: CPython 3.13.12; Pydantic 2.13.4, pytest 9.1.1,
LangGraph 1.2.11, LanceDB 0.37.1. `uv run --no-sync pytest -q`: **37 passed**,
14 pre-existing deprecation warnings (`table_names`, `create_fts_index`).
`uv run --no-sync shopguide --help` exposes index/ask/eval and exits successfully.
CLI help does not demonstrate model-backed ingestion or inference.

A separate `.venv-shopguide` uses CPython 3.11.15 and the updated `uv.lock` with
`dev`, `storage`, `shopguide`, `shopguide-dev` extras. The original `.venv` was not
replaced. New dependency constraints and the lockfile were updated together. Existing locked
package versions did not change; only the new optional development/storage
dependencies were added.

Hardware observed through `free -h` and `nvidia-smi`: approximately 503 GiB system
RAM; two NVIDIA GeForce RTX 3090 devices, 24,576 MiB each. No weights were loaded,
no GPUs reserved, and no inference capacity or model co-residency is inferred.

Validation and remaining gates are recorded in [implementation status](shopguide_status.md).
Raw baseline log: `artifacts/shopguide/m0/legacy-tests.txt`. Generated runtime,
benchmark sources, reports and local model configuration are excluded from Git.
