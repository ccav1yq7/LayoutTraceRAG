import json
import time
from contextlib import contextmanager

from pydantic import TypeAdapter
from sqlalchemy import text

from ..schemas import ID, Domain, new_id
from ..storage.snapshots import Snapshots
from ..tools.registry import arguments_hash

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "WAITING_INPUT", "WAITING_CONFIRMATION"}


class Sessions:
    def __init__(self, repository):
        self.repository = repository

    @contextmanager
    def transaction(self):
        with self.repository.engine.connect() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def _session(self, c, session_id, principal):
        row = (
            c.execute(
                text("SELECT * FROM sg_sessions WHERE id=:id"), {"id": session_id}
            )
            .mappings()
            .one_or_none()
        )
        if not row or row["principal"] != principal:
            raise PermissionError("SESSION_FORBIDDEN")
        return dict(row)

    def create(self, principal: str, domain: str, snapshot: str):
        TypeAdapter(ID).validate_python(principal)
        TypeAdapter(Domain).validate_python(domain)
        snapshots = Snapshots(self.repository)
        snapshots.readable(snapshot)
        if snapshots.get(snapshot)["domain"] != domain:
            raise ValueError("snapshot domain mismatch")
        sid = new_id("session")
        with self.transaction() as c:
            c.execute(
                text(
                    "INSERT INTO sg_sessions (id,principal,domain,snapshot,revision,task) VALUES (:id,:p,:d,:s,0,:t)"
                ),
                {
                    "id": sid,
                    "p": principal,
                    "d": domain,
                    "s": snapshot,
                    "t": new_id("task"),
                },
            )
        return self.get(sid, principal)

    def get(self, session_id, principal):
        with self.repository.engine.connect() as c:
            return self._session(c, session_id, principal)

    def select(self, session_id, principal, product, variant, expected_revision):
        with self.transaction() as c:
            session = self._session(c, session_id, principal)
            if session["revision"] != expected_revision:
                raise ValueError("SESSION_REVISION_CONFLICT")
            if session["active_run"]:
                raise RuntimeError("SESSION_BUSY")
            scope = self.repository.scope(
                principal, product, variant, session["snapshot"]
            )
            if scope.domain != session["domain"]:
                raise PermissionError("DOMAIN_MISMATCH")
            changed = session["product"] is not None and (
                session["product"],
                session["variant"],
            ) != (product, variant)
            task = new_id("task") if changed else session["task"]
            c.execute(
                text(
                    "UPDATE sg_sessions SET product=:p,variant=:v,task=:t,revision=revision+1 WHERE id=:id"
                ),
                {"p": product, "v": variant, "t": task, "id": session_id},
            )
        return self.get(session_id, principal)

    def submit(
        self,
        session_id,
        principal,
        client_message,
        question,
        expected_revision,
        *,
        attachment_ids=(),
        reply_to_step_id=None,
    ):
        TypeAdapter(ID).validate_python(client_message)
        if (
            not isinstance(question, str)
            or not question.strip()
            or len(question) > 8000
        ):
            raise ValueError("invalid message")
        payload: dict = {"question": question}
        if attachment_ids or reply_to_step_id:
            payload.update(
                attachment_ids=list(attachment_ids), reply_to_step_id=reply_to_step_id
            )
        fingerprint = arguments_hash(payload)
        with self.transaction() as c:
            session = self._session(c, session_id, principal)
            old = (
                c.execute(
                    text(
                        "SELECT * FROM sg_runs WHERE session=:s AND client_message=:m"
                    ),
                    {"s": session_id, "m": client_message},
                )
                .mappings()
                .one_or_none()
            )
            if old:
                if old["message_hash"] != fingerprint:
                    raise ValueError("MESSAGE_IDEMPOTENCY_CONFLICT")
                return self._decode(dict(old))
            if session["revision"] != expected_revision:
                raise ValueError("SESSION_REVISION_CONFLICT")
            if session["active_run"]:
                raise RuntimeError("SESSION_BUSY")
            active_count = c.execute(
                text(
                    "SELECT COUNT(*) FROM sg_runs r JOIN sg_sessions s ON r.session=s.id WHERE s.principal=:p AND r.status IN ('QUEUED','RUNNING')"
                ),
                {"p": principal},
            ).scalar_one()
            if active_count >= 2:
                raise RuntimeError("RATE_LIMITED")
            previous = (
                c.execute(
                    text(
                        "SELECT state,question,result FROM sg_runs WHERE session=:s AND task=:t AND status IN ('COMPLETED','WAITING_INPUT','WAITING_CONFIRMATION','FAILED') ORDER BY revision DESC LIMIT 3"
                    ),
                    {"s": session_id, "t": session["task"]},
                )
                .mappings()
                .all()
            )
            prior = json.loads(previous[0]["state"]) if previous else {}
            fulfilled = dict(prior.get("fulfilled_business_goals", {}))
            prior_result = (
                json.loads(previous[0]["result"])
                if previous and previous[0]["result"]
                else {}
            )
            receipt = prior_result.get("service_request", {})
            if receipt.get("ticket_id") and receipt.get("simulated"):
                active_applications = [
                    gid
                    for gid, goal in (prior.get("intent_state") or {})
                    .get("goals", {})
                    .items()
                    if goal["intent_id"] == "service.apply"
                    and goal["status"] == "active"
                ]
                if len(active_applications) == 1:
                    fulfilled[active_applications[0]] = receipt["ticket_id"]
            history = [
                {
                    "question": r["question"],
                    "answer": json.loads(r["result"]) if r["result"] else None,
                }
                for r in reversed(previous)
            ]
            rid = new_id("run")
            revision = expected_revision + 1
            state = {
                "run_id": rid,
                "session_id": session_id,
                "task_id": session["task"],
                "question": question,
                "product_id": session["product"],
                "attachment_ids": list(attachment_ids),
                "available_upload_ids": list(
                    dict.fromkeys(
                        prior.get("available_upload_ids", []) + list(attachment_ids)
                    )
                )[-4:],
                "inspected_upload_ids": prior.get("inspected_upload_ids", []),
                "turn_inspected_upload_ids": [],
                "reply_to_step_id": reply_to_step_id,
                "history": history,
                "intent_state": prior.get("intent_state"),
                "fulfilled_business_goals": fulfilled,
                "intent_ready": False,
                "intent_uncertain": prior.get("intent_uncertain", False),
                "pending": None,
                "evidence_ids": prior.get("evidence_ids", []),
                "inspected_asset_ids": prior.get("inspected_asset_ids", []),
                "observations": [],
                "visited_page_ids": [],
                "planner_steps": 0,
                "tool_attempts": 0,
                "model_calls": 0,
                "image_inputs": 0,
                "reported_tokens": 0,
                "usage_complete": True,
                "deadline": time.time() + 90,
                "last_tool": None,
                "repeats": 0,
                "parse_failures": 0,
                "repair": None,
                "status": "running",
            }
            c.execute(
                text(
                    "INSERT INTO sg_runs VALUES (:id,:s,:t,:m,:h,:q,:v,'QUEUED',0,:state,NULL)"
                ),
                {
                    "id": rid,
                    "s": session_id,
                    "t": session["task"],
                    "m": client_message,
                    "h": fingerprint,
                    "q": question,
                    "v": revision,
                    "state": json.dumps(state),
                },
            )
            c.execute(
                text("UPDATE sg_sessions SET revision=:v,active_run=:r WHERE id=:id"),
                {"v": revision, "r": rid, "id": session_id},
            )
        return self.run(rid, principal)

    def _decode(self, row):
        row["state"] = json.loads(row["state"])
        row["result"] = json.loads(row["result"]) if row["result"] else None
        return row

    def run(self, run_id, principal):
        with self.repository.engine.connect() as c:
            row = (
                c.execute(text("SELECT * FROM sg_runs WHERE id=:id"), {"id": run_id})
                .mappings()
                .one_or_none()
            )
            if not row:
                raise PermissionError("RUN_FORBIDDEN")
            self._session(c, row["session"], principal)
            return self._decode(dict(row))

    def claim(self, run_id, principal):
        with self.transaction() as c:
            row = (
                c.execute(text("SELECT * FROM sg_runs WHERE id=:id"), {"id": run_id})
                .mappings()
                .one_or_none()
            )
            if not row:
                raise PermissionError("RUN_FORBIDDEN")
            session = self._session(c, row["session"], principal)
            if row["status"] in TERMINAL:
                return self._decode(dict(row))
            if session["revision"] != row["revision"] or session["task"] != row["task"]:
                raise ValueError("RUN_CONTEXT_STALE")
            if session["active_run"] not in (None, run_id):
                raise RuntimeError("SESSION_BUSY")
            c.execute(
                text("UPDATE sg_runs SET status='RUNNING' WHERE id=:id"), {"id": run_id}
            )
            c.execute(
                text("UPDATE sg_sessions SET active_run=:r WHERE id=:s"),
                {"r": run_id, "s": row["session"]},
            )
        return self.run(run_id, principal)

    def save(self, run_id, principal, state):
        with self.transaction() as c:
            row = (
                c.execute(
                    text("SELECT session FROM sg_runs WHERE id=:id"), {"id": run_id}
                )
                .mappings()
                .one()
            )
            self._session(c, row["session"], principal)
            c.execute(
                text("UPDATE sg_runs SET state=:s WHERE id=:id"),
                {"s": json.dumps(state, allow_nan=False), "id": run_id},
            )

    def event(self, run_id, kind, payload):
        with self.transaction() as c:
            seq = c.execute(
                text("SELECT COALESCE(MAX(seq),0)+1 FROM sg_events WHERE run=:r"),
                {"r": run_id},
            ).scalar_one()
            c.execute(
                text("INSERT INTO sg_events VALUES (:r,:s,:k,:p)"),
                {"r": run_id, "s": seq, "k": kind, "p": json.dumps(payload)},
            )

    def events(self, run_id, principal):
        self.run(run_id, principal)
        with self.repository.engine.connect() as c:
            rows = (
                c.execute(
                    text(
                        "SELECT seq,kind,payload FROM sg_events WHERE run=:r ORDER BY seq"
                    ),
                    {"r": run_id},
                )
                .mappings()
                .all()
            )
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def finish(self, run_id, principal, state, status, result):
        if status not in TERMINAL:
            raise ValueError("invalid terminal state")
        with self.transaction() as c:
            row = (
                c.execute(
                    text("SELECT session FROM sg_runs WHERE id=:id"), {"id": run_id}
                )
                .mappings()
                .one()
            )
            self._session(c, row["session"], principal)
            c.execute(
                text("UPDATE sg_runs SET state=:s,status=:t,result=:r WHERE id=:id"),
                {
                    "s": json.dumps(state),
                    "t": status,
                    "r": json.dumps(result),
                    "id": run_id,
                },
            )
            c.execute(
                text(
                    "UPDATE sg_sessions SET active_run=NULL WHERE id=:s AND active_run=:r"
                ),
                {"s": row["session"], "r": run_id},
            )

    def cancel(self, run_id, principal):
        finalize = False
        with self.transaction() as c:
            row = (
                c.execute(text("SELECT * FROM sg_runs WHERE id=:id"), {"id": run_id})
                .mappings()
                .one_or_none()
            )
            if not row:
                raise PermissionError("RUN_FORBIDDEN")
            self._session(c, row["session"], principal)
            if row["status"] not in ("COMPLETED", "FAILED", "CANCELLED"):
                c.execute(
                    text("UPDATE sg_runs SET cancelled=1 WHERE id=:id"), {"id": run_id}
                )
                finalize = row["status"] != "RUNNING"
                state = json.loads(row["state"])
        if finalize:
            self.finish(run_id, principal, state, "CANCELLED", {"status": "cancelled"})
        return self.run(run_id, principal)
