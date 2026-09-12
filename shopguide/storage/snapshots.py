"""Durable state and atomic active pointer. Callers audit bytes before activation."""

import json
from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy import text

from ..schemas import ID, Domain


class Snapshots:
    def __init__(self, repository):
        self.repository = repository

    def get(self, snapshot_id: str):
        with self.repository.engine.connect() as c:
            row = (
                c.execute(
                    text("SELECT * FROM sg_snapshots WHERE id=:id"), {"id": snapshot_id}
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    def begin(self, snapshot_id: str, domain: str, identity: dict):
        TypeAdapter(ID).validate_python(snapshot_id)
        TypeAdapter(Domain).validate_python(domain)
        encoded = json.dumps(
            identity, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        with self.repository.engine.begin() as c:
            c.execute(
                text(
                    "INSERT OR IGNORE INTO sg_snapshots (id,domain,identity,state) VALUES (:id,:d,:i,'STAGING')"
                ),
                {"id": snapshot_id, "d": domain, "i": encoded},
            )
            row = (
                c.execute(
                    text("SELECT * FROM sg_snapshots WHERE id=:id"), {"id": snapshot_id}
                )
                .mappings()
                .one()
            )
            if row["identity"] != encoded or row["domain"] != domain:
                raise ValueError("snapshot identity mismatch")
            if row["state"] in ("FAILED", "AUDITING"):
                c.execute(
                    text(
                        "UPDATE sg_snapshots SET state='STAGING',error=NULL,manifest=NULL WHERE id=:id"
                    ),
                    {"id": snapshot_id},
                )
            elif row["state"] != "STAGING":
                raise ValueError("snapshot is immutable outside staging")

    def transition(
        self, snapshot_id: str, previous: str, target: str, manifest: dict | None = None
    ):
        if (previous, target) not in (("STAGING", "AUDITING"), ("AUDITING", "READY")):
            raise ValueError("invalid snapshot transition")
        if target == "READY" and not manifest:
            raise ValueError("audit manifest required")
        with self.repository.engine.begin() as c:
            r = c.execute(
                text(
                    "UPDATE sg_snapshots SET state=:target,manifest=:m WHERE id=:id AND state=:previous"
                ),
                {
                    "target": target,
                    "m": json.dumps(manifest, sort_keys=True) if manifest else None,
                    "id": snapshot_id,
                    "previous": previous,
                },
            )
            if r.rowcount != 1:
                raise ValueError("snapshot state conflict")

    def fail(self, snapshot_id: str):
        with self.repository.engine.begin() as c:
            c.execute(
                text(
                    "UPDATE sg_snapshots SET state='FAILED',error='BUILD_OR_AUDIT_FAILED' WHERE id=:id AND state IN ('STAGING','AUDITING')"
                ),
                {"id": snapshot_id},
            )

    def active(self, domain: str) -> str | None:
        with self.repository.engine.connect() as c:
            return c.execute(
                text("SELECT snapshot FROM sg_active WHERE domain=:d"), {"d": domain}
            ).scalar_one_or_none()

    def readable(self, snapshot_id: str, *, allow_legacy: bool = False):
        row = self.get(snapshot_id)
        if row is None and allow_legacy:
            return
        if row is None or row["state"] not in ("READY", "ACTIVE"):
            raise PermissionError("snapshot has not passed audit")

    def publish(self, snapshot_id: str, *, expected_active: str | None):
        # BEGIN IMMEDIATE serializes pointer CAS with concurrent publishers.
        with self.repository.engine.connect() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                row = (
                    c.execute(
                        text("SELECT * FROM sg_snapshots WHERE id=:id"),
                        {"id": snapshot_id},
                    )
                    .mappings()
                    .one()
                )
                if row["state"] != "READY" or not row["manifest"]:
                    raise ValueError("only audited READY snapshots can be published")
                current = c.execute(
                    text("SELECT snapshot FROM sg_active WHERE domain=:d"),
                    {"d": row["domain"]},
                ).scalar_one_or_none()
                if current != expected_active:
                    raise ValueError("active pointer changed; re-evaluate publication")
                if current:
                    c.execute(
                        text("UPDATE sg_snapshots SET state='READY' WHERE id=:id"),
                        {"id": current},
                    )
                c.execute(
                    text(
                        "INSERT INTO sg_active VALUES (:d,:id) ON CONFLICT(domain) DO UPDATE SET snapshot=excluded.snapshot"
                    ),
                    {"d": row["domain"], "id": snapshot_id},
                )
                c.execute(
                    text("UPDATE sg_snapshots SET state='ACTIVE' WHERE id=:id"),
                    {"id": snapshot_id},
                )
                c.commit()
            except BaseException:
                c.rollback()
                raise


def generation_path(root: Path, snapshot_id: str) -> Path:
    TypeAdapter(ID).validate_python(snapshot_id)
    base = root.resolve()
    path = base / snapshot_id
    if not path.resolve().is_relative_to(base):
        raise ValueError("snapshot path escapes root")
    return path
