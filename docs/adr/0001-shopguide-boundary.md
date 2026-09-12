# ADR 0001: ShopGuide M1 boundary

Date: 2026-09-09. Status: accepted for implementation.

Preserve the current dirty workspace and legacy video interfaces. Implement V2
under `shopguide`; use a separate SQLite database and content-addressed
asset root. SQLAlchemy 2 and packaged Alembic revisions manage metadata. The vector
index and ACTIVE snapshot publication belong to M2; no old LanceDB table is changed.

Acceptance before edits: strict product/locator/action/answer contracts; positive
area normalized coordinates and reversible rotated CropBox mapping; immutable
document versions; assets retain source hash and parent lineage; authenticated
catalog scope and asset reads deny other users, products, snapshots and revoked
versions; tool arguments validate before handlers and hash deterministically;
fake gateways are labeled and formal settings reject missing revisions/manifests.
Legacy tests and wheel installation must still work. Tests map to PLAN U-01–06,
U-10, U-13 and U-17. M1 does not claim semantic verification, runtime agent loops,
PDF ingestion, HTTP APIs, benchmark inference or model performance.

M0 external data/model blockers do not prevent isolated M1 contract development;
they remain explicit gates for real inference and official benchmark claims.
The actual parent-repository HEAD differs from PLAN's source review SHA; current
file hashes, status and lockfile are saved under `artifacts/shopguide/m0/` before
edits. No pre-existing changes are reverted or committed.
