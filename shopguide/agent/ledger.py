"""At-most-once local effects through idempotent handlers and explicit reconciliation."""

import json
import time

from sqlalchemy import bindparam, text

from ..schemas import OrderItem, new_id
from ..tools.registry import arguments_hash


class OutcomeUnknown(RuntimeError):
    pass


class ToolLedger:
    def __init__(self, sessions):
        self.sessions = sessions

    def execute(
        self, run_id, name, arguments, handler, *, side_effect=False, recover=None
    ):
        fingerprint = arguments_hash(arguments)
        with self.sessions.transaction() as c:
            row = (
                c.execute(
                    text(
                        "SELECT * FROM sg_tool_calls WHERE run=:r AND name=:n AND arguments_hash=:h"
                    ),
                    {"r": run_id, "n": name, "h": fingerprint},
                )
                .mappings()
                .one_or_none()
            )
            if row:
                call_id = row["id"]
                state = row["state"]
                if state in ("SUCCEEDED", "FAILED"):
                    return json.loads(row["result"]), False, call_id
                if side_effect:
                    # Recover outside this transaction; never replay uncertain writes.
                    state = "RECOVER"
            else:
                call_id = new_id("call")
                state = "NEW"
                c.execute(
                    text(
                        "INSERT INTO sg_tool_calls VALUES (:id,:r,:n,:h,'PENDING',NULL,:e)"
                    ),
                    {
                        "id": call_id,
                        "r": run_id,
                        "n": name,
                        "h": fingerprint,
                        "e": side_effect,
                    },
                )
            if state != "RECOVER":
                c.execute(
                    text("UPDATE sg_tool_calls SET state='RUNNING' WHERE id=:id"),
                    {"id": call_id},
                )
        if state == "RECOVER":
            result = recover() if recover else None
            if result is None:
                self._save(
                    call_id,
                    "OUTCOME_UNKNOWN",
                    {"status": "error", "code": "OUTCOME_UNKNOWN"},
                )
                return {"status": "error", "code": "OUTCOME_UNKNOWN"}, False, call_id
            self._save(call_id, "SUCCEEDED", result)
            return result, False, call_id
        try:
            result = handler()
        except OutcomeUnknown:
            self._save(
                call_id,
                "OUTCOME_UNKNOWN",
                {"status": "error", "code": "OUTCOME_UNKNOWN"},
            )
            return {"status": "error", "code": "OUTCOME_UNKNOWN"}, True, call_id
        except (
            ValueError,
            TypeError,
            OSError,
            KeyError,
            PermissionError,
            TimeoutError,
            RuntimeError,
        ):
            if side_effect:
                self._save(
                    call_id,
                    "OUTCOME_UNKNOWN",
                    {"status": "error", "code": "OUTCOME_UNKNOWN"},
                )
                return {"status": "error", "code": "OUTCOME_UNKNOWN"}, True, call_id
            result = {"status": "error", "code": "TOOL_EXECUTION_FAILED"}
            self._save(call_id, "FAILED", result)
            return result, True, call_id
        self._save(call_id, "SUCCEEDED", result)
        return result, True, call_id

    def _save(self, call_id, state, result):
        with self.sessions.transaction() as c:
            c.execute(
                text("UPDATE sg_tool_calls SET state=:s,result=:r WHERE id=:id"),
                {"s": state, "r": json.dumps(result, allow_nan=False), "id": call_id},
            )


class SimulatedService:
    def __init__(self, sessions):
        self.sessions = sessions

    def query(self, run_id, principal, ticket_id=None):
        """Read local creation facts only; do not invent repair lifecycle updates."""
        from .contracts import QueryServiceArgs

        ticket_id = QueryServiceArgs(ticket_id=ticket_id).ticket_id
        run = self.sessions.run(run_id, principal)
        session = self.sessions.get(run["session"], principal)
        if session["domain"] != "demo":
            raise PermissionError("SIMULATED_DEMO_ONLY")
        if not session["product"] or not session["variant"]:
            raise ValueError("PRODUCT_REQUIRED")
        items = [
            o.order_item_id
            for o in self.sessions.repository.orders(principal)
            if o.product_id == session["product"] and o.variant_id == session["variant"]
        ]
        tickets = []
        more = False
        if items:
            statement = text(
                "SELECT id,arguments FROM sg_tickets WHERE principal=:p "
                "AND json_extract(arguments,'$.related_order_item') IN :items "
                "AND (:ticket IS NULL OR id=:ticket) ORDER BY id LIMIT 21"
            ).bindparams(bindparam("items", expanding=True))
            with self.sessions.repository.engine.connect() as c:
                rows = (
                    c.execute(
                        statement, {"p": principal, "items": items, "ticket": ticket_id}
                    )
                    .mappings()
                    .all()
                )
            more = len(rows) > 20
            for row in rows[:20]:
                arguments = json.loads(row["arguments"])
                tickets.append(
                    {
                        "ticket_id": row["id"],
                        "state": "created",
                        "state_label": "模拟工单已创建",
                        "summary": arguments["summary"],
                        "related_order_item": arguments["related_order_item"],
                    }
                )
        return {
            "status": "ok" if tickets else "empty",
            "simulated": True,
            "tickets": tickets,
            "has_more": more,
            "message": "仅查询本地模拟工单的创建记录，未接入真实维修进度。"
            if tickets
            else "未查询到当前商品下可查看的模拟工单。",
        }

    def prepare(self, run_id, principal, arguments, *, ttl=300):
        run = self.sessions.run(run_id, principal)
        session = self.sessions.get(run["session"], principal)
        if session["domain"] != "demo":
            raise PermissionError("SIMULATED_DEMO_ONLY")
        order = self.sessions.repository.get(OrderItem, arguments["related_order_item"])
        if (
            order.principal_id != principal
            or order.product_id != session["product"]
            or order.variant_id != session["variant"]
        ):
            raise PermissionError("ORDER_FORBIDDEN")
        fingerprint = arguments_hash(arguments)
        with self.sessions.transaction() as c:
            existing = c.execute(
                text(
                    "SELECT id FROM sg_confirmations WHERE run=:r AND arguments_hash=:h"
                ),
                {"r": run_id, "h": fingerprint},
            ).scalar_one_or_none()
            confirmation = existing or new_id("confirmation")
            if existing is None:
                c.execute(
                    text(
                        "INSERT INTO sg_confirmations VALUES (:id,:r,:s,:t,:p,:v,:h,:a,:e,0)"
                    ),
                    {
                        "id": confirmation,
                        "r": run_id,
                        "s": session["id"],
                        "t": session["task"],
                        "p": principal,
                        "v": session["revision"],
                        "h": fingerprint,
                        "a": json.dumps(arguments),
                        "e": time.time() + ttl,
                    },
                )
        return {
            "status": "ok",
            "confirmation_id": confirmation,
            "arguments_hash": fingerprint,
            "summary": arguments,
            "requires_confirmation": True,
        }

    def _check(self, c, confirmation, principal, *, approved=False):
        row = (
            c.execute(
                text("SELECT * FROM sg_confirmations WHERE id=:id"),
                {"id": confirmation},
            )
            .mappings()
            .one_or_none()
        )
        if not row or row["principal"] != principal:
            raise PermissionError("CONFIRMATION_FORBIDDEN")
        session = self.sessions._session(c, row["session"], principal)
        if row["task"] != session["task"] or row["revision"] != session["revision"]:
            raise ValueError("CONFIRMATION_STALE")
        if time.time() > row["expires"]:
            raise ValueError("CONFIRMATION_EXPIRED")
        if approved and not row["approved"]:
            raise PermissionError("CONFIRMATION_REQUIRED")
        return dict(row), session

    def approve(self, confirmation, principal, expected_hash, expected_revision):
        with self.sessions.transaction() as c:
            row, session = self._check(c, confirmation, principal)
            if (
                expected_hash != row["arguments_hash"]
                or expected_revision != row["revision"]
            ):
                raise ValueError("CONFIRMATION_ARGUMENTS_CHANGED")
            run = (
                c.execute(
                    text("SELECT * FROM sg_runs WHERE id=:id"), {"id": row["run"]}
                )
                .mappings()
                .one()
            )
            if run["status"] != "WAITING_CONFIRMATION" or run["cancelled"]:
                raise ValueError("RUN_NOT_WAITING_CONFIRMATION")
            if session["active_run"] not in (None, row["run"]):
                raise RuntimeError("SESSION_BUSY")
            state = json.loads(run["state"])
            state["deadline"] = time.time() + 90
            state["status"] = "running"
            c.execute(
                text("UPDATE sg_confirmations SET approved=1 WHERE id=:id"),
                {"id": confirmation},
            )
            c.execute(
                text(
                    "UPDATE sg_runs SET status='QUEUED',state=:s,result=NULL WHERE id=:id"
                ),
                {"s": json.dumps(state), "id": row["run"]},
            )
            c.execute(
                text("UPDATE sg_sessions SET active_run=:r WHERE id=:s"),
                {"r": row["run"], "s": row["session"]},
            )
        return row["run"]

    def check_approved(self, confirmation, principal, run_id):
        with self.sessions.repository.engine.connect() as c:
            row, _session = self._check(c, confirmation, principal, approved=True)
            if row["run"] != run_id:
                raise PermissionError("CONFIRMATION_RUN_MISMATCH")

    def commit(self, confirmation, principal, run_id):
        with self.sessions.transaction() as c:
            row, _session = self._check(c, confirmation, principal, approved=True)
            if row["run"] != run_id:
                raise PermissionError("CONFIRMATION_RUN_MISMATCH")
            run = (
                c.execute(
                    text("SELECT cancelled,status FROM sg_runs WHERE id=:id"),
                    {"id": run_id},
                )
                .mappings()
                .one()
            )
            if run["cancelled"] or run["status"] != "RUNNING":
                raise PermissionError("RUN_NOT_EXECUTABLE")
            ticket = c.execute(
                text("SELECT id FROM sg_tickets WHERE confirmation=:id"),
                {"id": confirmation},
            ).scalar_one_or_none()
            if ticket is None:
                ticket = new_id("ticket")
                c.execute(
                    text("INSERT INTO sg_tickets VALUES (:id,:c,:p,:a)"),
                    {
                        "id": ticket,
                        "c": confirmation,
                        "p": principal,
                        "a": row["arguments"],
                    },
                )
        return {"status": "ok", "ticket_id": ticket, "simulated": True}

    def recover(self, confirmation, principal):
        with self.sessions.repository.engine.connect() as c:
            row = (
                c.execute(
                    text(
                        "SELECT id FROM sg_tickets WHERE confirmation=:c AND principal=:p"
                    ),
                    {"c": confirmation, "p": principal},
                )
                .mappings()
                .one_or_none()
            )
        return (
            {"status": "ok", "ticket_id": row["id"], "simulated": True} if row else None
        )
