import hashlib
import json
import math
from pathlib import Path

import lancedb
from lancedb.index import FTS

from ..schemas import Evidence, ManualLocator, SearchScope
from ..storage.repository import AssetRepository
from ..storage.snapshots import Snapshots, generation_path
from .models import Embedder, Reranker


def row_digest(rows):
    return hashlib.sha256(
        json.dumps(
            sorted(rows, key=lambda r: r["id"]),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def vectors_valid(vectors, count, dimension):
    if len(vectors) != count:
        raise ValueError("embedding count mismatch")
    for vector in vectors:
        if (
            len(vector) != dimension
            or not all(math.isfinite(v) for v in vector)
            or not any(vector)
        ):
            raise ValueError("nonfinite/zero vector or embedding dimension mismatch")


def literal(value: str):
    return "'" + value.replace("'", "''") + "'"


class ScopedIndex:
    def __init__(
        self,
        repository,
        asset_root: Path,
        index_root: Path,
        snapshot_id: str,
        embedder: Embedder,
    ):
        self.repository = repository
        self.asset_root = asset_root
        self.snapshot = snapshot_id
        self.embedder = embedder
        self.path = generation_path(index_root, snapshot_id)
        self.snapshots = Snapshots(repository)

    def _table(self):
        return lancedb.connect(str(self.path)).open_table("evidence_v2")

    def build(self, evidence: list[Evidence]):
        row = self.snapshots.get(self.snapshot)
        if not row or row["state"] != "STAGING":
            raise ValueError("STAGING snapshot required")
        try:
            if not evidence:
                raise ValueError("empty snapshot cannot be activated")
            unique = {e.evidence_id: e for e in evidence}
            if len(unique) != len(evidence):
                raise ValueError("duplicate evidence IDs")
            vectors = self.embedder.embed_documents([e.text for e in evidence])
            vectors_valid(vectors, len(evidence), self.embedder.identity.dimension)
            records = []
            for ev, vector in zip(evidence, vectors, strict=True):
                if ev != self.repository.get(Evidence, ev.evidence_id):
                    raise ValueError("unregistered evidence")
                if ev.scope.snapshot_id != self.snapshot or not isinstance(
                    ev.source_locator, ManualLocator
                ):
                    raise ValueError("snapshot/manual identity mismatch")
                if len(ev.scope.authorized_product_ids) != 1:
                    raise ValueError("each evidence row must bind one product")
                self.repository.authorize(ev.scope)
                records.append(
                    {
                        "id": ev.evidence_id,
                        "text": ev.text,
                        "vector": vector,
                        "domain": ev.scope.domain,
                        "principal": ev.scope.principal_id,
                        "product": ev.scope.authorized_product_ids[0],
                        "variant": ev.scope.confirmed_variant,
                        "document": ev.source_locator.doc_version_id,
                        "snapshot": self.snapshot,
                        "language": ev.scope.language_policy[0],
                        "payload": ev.model_dump_json(),
                        "embedding_identity": self.embedder.identity.model_dump_json(),
                    }
                )
            self.path.mkdir(parents=True, exist_ok=True)
            table = lancedb.connect(str(self.path)).create_table(
                "evidence_v2", data=records, mode="overwrite"
            )
            table.create_index("text", config=FTS(), replace=True)
            self.snapshots.transition(self.snapshot, "STAGING", "AUDITING")
            # Always audit an independently reopened table and actual asset bytes.
            reopened = self._table()
            rows = reopened.to_arrow().to_pylist()
            vectors_valid(
                [r["vector"] for r in rows],
                len(evidence),
                self.embedder.identity.dimension,
            )
            if len(rows) != len(evidence) or not any(
                i.index_type == "FTS" and i.columns == ["text"]
                for i in reopened.list_indices()
            ):
                raise ValueError("incomplete index")
            assets = AssetRepository(
                self.repository, self.asset_root, staging_snapshot=self.snapshot
            )
            checked = set()
            for ev in evidence:
                for aid in ev.asset_ids:
                    assets.read(aid, ev.scope)
                    checked.add(aid)
            # Real FTS path must be functional, even for a no-match query.
            reopened.search("shopguidehealthprobe", query_type="fts").limit(1).to_list()
            manifest = {
                "row_count": len(rows),
                "asset_count": len(checked),
                "row_sha256": row_digest(rows),
                "identity": self.embedder.identity.model_dump(mode="json"),
            }
            self.snapshots.transition(self.snapshot, "AUDITING", "READY", manifest)
            return manifest
        except BaseException:
            self.snapshots.fail(self.snapshot)
            raise

    def health(self):
        self.snapshots.readable(self.snapshot)
        state = self.snapshots.get(self.snapshot)
        manifest = json.loads(state["manifest"])
        if manifest["identity"] != self.embedder.identity.model_dump(mode="json"):
            raise ValueError("INDEX_IDENTITY_MISMATCH")
        table = self._table()
        rows = table.to_arrow().to_pylist()
        if (
            len(rows) != manifest["row_count"]
            or row_digest(rows) != manifest["row_sha256"]
        ):
            raise ValueError("index content/count mismatch")
        if any(
            r["embedding_identity"] != self.embedder.identity.model_dump_json()
            for r in rows
        ):
            raise ValueError("INDEX_IDENTITY_MISMATCH")
        vectors_valid(
            [r["vector"] for r in rows], len(rows), self.embedder.identity.dimension
        )
        if not any(
            i.index_type == "FTS" and i.columns == ["text"]
            for i in table.list_indices()
        ):
            raise ValueError("missing FTS index")
        return manifest

    def publish(self, *, expected_active: str | None):
        self.health()
        # Recheck assets at publication; old pointer survives any failure.
        assets = AssetRepository(self.repository, self.asset_root)
        for row in self._table().to_arrow().to_pylist():
            ev = self.repository.get(Evidence, row["id"])
            for aid in ev.asset_ids:
                assets.read(aid, ev.scope)
        self.snapshots.publish(self.snapshot, expected_active=expected_active)

    def _scope(self, scope: SearchScope):
        # Revalidate because model_copy / model_construct bypass Pydantic checks.
        scope = SearchScope.model_validate_json(scope.model_dump_json())
        self.repository.authorize(scope)
        if scope.snapshot_id != self.snapshot:
            raise PermissionError("wrong snapshot")
        self.snapshots.readable(self.snapshot)
        return scope

    def _filter(self, scope):
        singles = {
            "domain": scope.domain,
            "principal": scope.principal_id,
            "variant": scope.confirmed_variant,
            "snapshot": scope.snapshot_id,
        }
        parts = [f"{key} = {literal(value)}" for key, value in singles.items()]
        for key, values in [
            ("product", scope.authorized_product_ids),
            ("document", scope.allowed_doc_version_ids),
            ("language", scope.language_policy),
        ]:
            parts.append(f"{key} IN ({','.join(literal(v) for v in values)})")
        return " AND ".join(parts)

    def channel(self, query: str, scope: SearchScope, *, channel: str, k: int = 30):
        scope = self._scope(scope)
        if not query.strip() or not 1 <= k <= 100:
            raise ValueError("query/k out of bounds")
        self.health()
        table = self._table()
        if channel == "dense":
            vector = self.embedder.embed_query(query)
            vectors_valid([vector], 1, self.embedder.identity.dimension)
            builder = table.search(vector, query_type="vector").distance_type("cosine")
        elif channel == "fts":
            builder = table.search(query, query_type="fts")
        else:
            raise ValueError("unknown retrieval channel")
        rows = builder.where(self._filter(scope), prefilter=True).limit(k).to_list()
        results = []
        for row in rows:
            ev = self.repository.get(Evidence, row["id"])
            self.repository.authorize(ev.scope)
            if row["payload"] != ev.model_dump_json() or row["text"] != ev.text:
                raise ValueError("index differs from authoritative evidence")
            if (
                ev.scope.principal_id != scope.principal_id
                or ev.scope.domain != scope.domain
                or ev.scope.snapshot_id != scope.snapshot_id
                or ev.scope.confirmed_variant != scope.confirmed_variant
                or not set(ev.scope.authorized_product_ids)
                <= set(scope.authorized_product_ids)
                or not set(ev.scope.allowed_doc_version_ids)
                <= set(scope.allowed_doc_version_ids)
            ):
                raise PermissionError("corrupt index scope")
            results.append(ev)
        return results

    def search(
        self,
        query: str,
        scope: SearchScope,
        *,
        reranker: Reranker,
        candidates: int = 30,
        k: int = 8,
        require_assets: bool = True,
    ):
        if not 1 <= k <= candidates <= 100:
            raise ValueError("invalid candidate budgets")
        if (
            self.embedder.identity.model_mode == "real"
            and reranker.model_mode != "real"
        ):
            raise ValueError("real profile cannot silently use fake reranker")
        dense = self.channel(query, scope, channel="dense", k=candidates)
        lexical = self.channel(query, scope, channel="fts", k=candidates)
        scores: dict[str, float] = {}
        evidence = {}
        traces: dict[str, list[dict]] = {}
        for name, items in [("dense", dense), ("fts", lexical)]:
            for rank, ev in enumerate(items, 1):
                evidence[ev.evidence_id] = ev
                scores[ev.evidence_id] = scores.get(ev.evidence_id, 0.0) + 1 / (
                    60 + rank
                )
                traces.setdefault(ev.evidence_id, []).append(
                    {"channel": name, "rank": rank}
                )
        ordered = sorted(evidence, key=lambda eid: (-scores[eid], eid))[
            : 2 * candidates
        ]
        if not ordered:
            return []
        rescored = reranker.scores(
            query, [evidence[e].text for e in ordered], [scores[e] for e in ordered]
        )
        if len(rescored) != len(ordered) or not all(math.isfinite(v) for v in rescored):
            raise ValueError("invalid reranker scores")
        ranked = sorted(
            zip(ordered, rescored, strict=True), key=lambda pair: (-pair[1], pair[0])
        )[:k]
        assets = AssetRepository(self.repository, self.asset_root)
        result = []
        for eid, score in ranked:
            ev = evidence[eid]
            if require_assets:
                for aid in ev.asset_ids:
                    assets.read(aid, scope)
            result.append(
                {
                    "evidence": ev,
                    "score": score,
                    "retrieval_trace": traces[eid],
                    "model_mode": self.embedder.identity.model_mode,
                }
            )
        return result
