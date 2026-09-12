import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from ..agent.ledger import SimulatedService
from ..agent.runtime import AgentRunner
from ..sessions.store import TERMINAL, Sessions


class HealthGateway:
    def __init__(self, inner, health):
        self.inner = inner
        self.health = health
        self.model_id = inner.model_id
        self.model_mode = inner.model_mode
        self.provider = getattr(inner, "provider", "fixture")
        self.supports_images = getattr(inner, "supports_images", True)
        self.timeout = getattr(inner, "timeout", 25)

    def complete(self, *args):
        import copy

        gateway = copy.copy(self.inner)
        if hasattr(gateway, "timeout"):
            gateway.timeout = min(gateway.timeout, self.timeout)
        try:
            reply = gateway.complete(*args)
        except (OSError, RuntimeError, ValueError, TypeError):
            self.health["failed_until"] = time.monotonic() + 30
            raise
        self.health["failed_until"] = 0
        self.health["verified"] = True
        return reply


class Worker:
    def __init__(
        self,
        repository,
        root,
        index_factory,
        reranker,
        gateway_factory,
        *,
        enabled=True,
    ):
        self.sessions = Sessions(repository)
        self.root = root
        self.index_factory = index_factory
        self.reranker = reranker
        self.gateway_factory = gateway_factory
        self.enabled = enabled
        self.health = {"failed_until": 0, "verified": False}
        self.lock = threading.Lock()
        self.jobs = {}
        self.closed = False
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="shopguide")

    def available(self):
        with self.lock:
            return (
                not self.closed
                and self.health["failed_until"] <= time.monotonic()
                and len(self.jobs) < 16
            )

    def schedule(self, run_id, principal):
        if not self.enabled:
            return
        with self.lock:
            if run_id in self.jobs:
                return
            if len(self.jobs) >= 16:
                raise RuntimeError("QUEUE_FULL")
            self.jobs[run_id] = None
        future = self.pool.submit(self._run, run_id, principal)
        with self.lock:
            self.jobs[run_id] = future
        future.add_done_callback(lambda f: self._done(run_id, principal))

    def _done(self, run_id, principal):
        with self.lock:
            self.jobs.pop(run_id, None)
            closed = self.closed
        if not closed:
            record = self.sessions.run(run_id, principal)
            if record["status"] == "QUEUED":
                self.schedule(run_id, principal)

    def _run(self, run_id, principal):
        try:
            gateway = HealthGateway(self.gateway_factory(), self.health)
            AgentRunner(
                self.sessions.repository,
                self.root,
                self.index_factory,
                self.reranker,
                gateway,
            ).execute(run_id, principal)
        except Exception:  # noqa: BLE001 - worker boundary must close failed runs without leaking details
            run = self.sessions.run(run_id, principal)
            if run["status"] not in TERMINAL:
                result = {"status": "failed", "code": "WORKER_EXECUTION_FAILED"}
                pending = run["state"].get("pending") or {}
                if pending.get("tool_name") == "commit_service_request":
                    effect = SimulatedService(self.sessions).recover(
                        pending["arguments"]["confirmation_id"], principal
                    )
                    if effect:
                        result["service_request"] = effect
                self.sessions.finish(run_id, principal, run["state"], "FAILED", result)
                self.sessions.event(run_id, "run.failed", {})

    def recover(self):
        with self.sessions.repository.engine.connect() as c:
            rows = (
                c.execute(
                    text(
                        "SELECT r.id,s.principal FROM sg_runs r JOIN sg_sessions s ON r.session=s.id WHERE r.status IN ('QUEUED','RUNNING')"
                    )
                )
                .mappings()
                .all()
            )
        for row in rows:
            if self.available():
                self.schedule(row["id"], row["principal"])

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=True)
