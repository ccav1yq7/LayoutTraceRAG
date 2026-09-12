import asyncio
import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..agent.ledger import SimulatedService
from ..schemas import (
    ID,
    Asset,
    DocumentVersion,
    Evidence,
    GuideAnswer,
    ManualLocator,
    Product,
    new_id,
)
from ..sessions.store import TERMINAL, Sessions
from ..storage.locking import RootLock
from ..storage.repository import AssetRepository
from ..storage.snapshots import Snapshots
from .auth import Auth
from .uploads import MAX_BYTES, Uploads
from .worker import Worker


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateSession(Input):
    pass


class SelectProduct(Input):
    product_id: ID
    variant_id: ID
    expected_revision: int = Field(ge=0)


class Message(Input):
    client_message_id: ID
    expected_session_revision: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=8000)
    attachment_ids: list[ID] = Field(default_factory=list, max_length=4)
    reply_to_step_id: ID | None = None


class Confirm(Input):
    confirmation_id: ID
    arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_session_revision: int = Field(ge=0)


class Feedback(Input):
    run_id: ID
    reason: str = Field(pattern=r"^(answer|model|image|steps)$")
    note: str = Field(default="", max_length=1000)


PROGRESS = {
    "plan": "理解问题",
    "inspect": "查看图片",
    "select": "选择原图",
    "write": "整理步骤",
    "verify": "核验依据",
    "search_manuals": "查找说明书",
    "read_page": "读取页面",
    "inspect_asset": "查看原图",
    "inspect_user_image": "查看上传图片",
    "list_purchased_items": "确认商品",
    "resolve_product": "核对型号",
    "prepare_service_request": "准备模拟申请",
    "commit_service_request": "提交模拟申请",
}


def create_app(
    repository,
    root: Path,
    index_factory,
    reranker,
    gateway_factory,
    *,
    demo=False,
    principal="user_demo",
    snapshot="snapshot_web_demo",
    web_dist: Path | None = None,
    worker_enabled=True,
):
    sessions = Sessions(repository)
    auth = Auth(repository)
    assets = AssetRepository(repository, root / "assets")
    uploads = Uploads(repository, root / "uploads")
    worker = Worker(
        repository,
        root,
        index_factory,
        reranker,
        gateway_factory,
        enabled=worker_enabled,
    )
    probe = gateway_factory()
    mode = (
        "sample"
        if probe.model_mode == "fake"
        else "live_model_test_retrieval"
        if index_factory(snapshot).embedder.identity.model_mode == "fake"
        else "live"
    )

    @asynccontextmanager
    async def lifespan(app):
        with RootLock(root, exclusive=True, service=True):
            try:
                worker.recover()
                yield
            finally:
                await asyncio.to_thread(worker.close)

    app = FastAPI(
        title="ShopGuide API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.auth = auth
    app.state.worker = worker
    app.state.sessions = sessions
    app.state.uploads = uploads
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]
    )

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/v1/", "/health/")):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.exception_handler(PermissionError)
    async def forbidden(request, error):
        return JSONResponse(
            {"detail": {"code": "RESOURCE_UNAVAILABLE"}}, status_code=404
        )

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse(
            {"detail": {"code": "RESOURCE_UNAVAILABLE"}}, status_code=404
        )

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request, error):
        return JSONResponse({"detail": {"code": "SOURCE_UNAVAILABLE"}}, status_code=410)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        code = str(error)
        safe = (
            code
            if code
            in (
                "SESSION_REVISION_CONFLICT",
                "MESSAGE_IDEMPOTENCY_CONFLICT",
                "CONFIRMATION_STALE",
                "CONFIRMATION_EXPIRED",
                "CONFIRMATION_ARGUMENTS_CHANGED",
                "UPLOAD_SIZE",
                "UPLOAD_PIXELS",
                "UPLOAD_FORMAT",
            )
            else "INVALID_REQUEST"
        )
        return JSONResponse(
            {"detail": {"code": safe}},
            status_code=413
            if safe in ("UPLOAD_SIZE", "UPLOAD_PIXELS")
            else 415
            if safe == "UPLOAD_FORMAT"
            else 409,
        )

    @app.exception_handler(RuntimeError)
    async def runtime_error(request, error):
        code = str(error)
        return JSONResponse(
            {
                "detail": {
                    "code": code
                    if code in ("SESSION_BUSY", "RATE_LIMITED", "QUEUE_FULL")
                    else "SERVICE_UNAVAILABLE"
                }
            },
            status_code=429
            if code in ("RATE_LIMITED", "QUEUE_FULL")
            else 409
            if code == "SESSION_BUSY"
            else 503,
        )

    def identity(request: Request):
        return auth.identity(request)

    user_dependency = Depends(identity)

    def result_for_delivery(record, owner):
        result = copy.deepcopy(record["result"])
        if result and result.get("answer"):
            try:
                answer = GuideAnswer.model_validate_json(json.dumps(result["answer"]))
                for uid in result.get("used_upload_ids", []):
                    uploads.read(uid, owner, record["session"], record["task"])
                source_entries = []
                for citation in answer.citations:
                    ev = repository.get(Evidence, citation.evidence_id)
                    if (
                        ev.scope.principal_id != owner
                        or ev.source_locator != citation.source_locator
                        or answer.product_ref not in ev.scope.authorized_product_ids
                    ):
                        raise PermissionError("source scope mismatch")
                    repository.authorize(ev.scope)
                    Snapshots(repository).readable(ev.scope.snapshot_id)
                    source_entries.append(
                        {
                            "evidence_id": ev.evidence_id,
                            "asset_ids": list(ev.asset_ids),
                            "page_index_0based": ev.source_locator.page_index_0based,
                        }
                    )
                result["sources"] = source_entries
                for step in answer.steps:
                    for aid in step.display_asset_ids:
                        asset = repository.get(Asset, aid)
                        if asset.scope.principal_id != owner:
                            raise PermissionError("asset owner mismatch")
                        assets.read(aid, asset.scope)
            except (PermissionError, KeyError, ValueError, OSError):
                result = {
                    "status": "source_unavailable",
                    "message": "这条答复引用的资料已失效或不可读取，请重新查询。",
                }
        return result

    def public_run(record, owner):
        return {
            "run_id": record["id"],
            "session_id": record["session"],
            "client_message_id": record["client_message"],
            "revision": record["revision"],
            "task_id": record["task"],
            "question": record["question"],
            "status": record["status"],
            "product_id": record["state"].get("product_id"),
            "attachment_ids": record["state"].get("attachment_ids", []),
            "reply_to_step_id": record["state"].get("reply_to_step_id"),
            "result": result_for_delivery(record, owner),
        }

    @app.post("/v1/auth/demo")
    def demo_login(request: Request, response: Response):
        if (
            not demo
            or request.client is None
            or request.client.host not in ("127.0.0.1", "::1")
        ):
            raise HTTPException(403, detail={"code": "LOCAL_DEMO_ONLY"})
        auth.origin(request)
        token, csrf = auth.issue(principal)
        response.set_cookie(
            "sg_auth",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=28800,
        )
        return {
            "csrf": csrf,
            "demo": True,
            "mode": mode,
            "user_id": principal,
            "display_name": "演示用户",
        }

    @app.get("/v1/me")
    def me(user=user_dependency):
        return {
            "csrf": user["csrf"],
            "demo": demo,
            "mode": mode,
            "user_id": user["principal"],
            "display_name": "演示用户" if demo else "当前用户",
        }

    @app.get("/v1/catalog/purchases")
    def catalog(user=user_dependency):
        result = []
        for order in repository.orders(user["principal"]):
            product = repository.get(Product, order.product_id)
            result.append(
                {
                    "order_item_id": order.order_item_id,
                    "product_id": product.product_id,
                    "variant_id": order.variant_id,
                    "brand": product.brand,
                    "model": product.model,
                    "category": product.category,
                }
            )
        return {"items": result}

    @app.get("/v1/sessions")
    def session_list(user=user_dependency):
        with repository.engine.connect() as c:
            rows = (
                c.execute(
                    text(
                        "SELECT id,product,variant,revision,task FROM sg_sessions WHERE principal=:p ORDER BY rowid DESC LIMIT 30"
                    ),
                    {"p": user["principal"]},
                )
                .mappings()
                .all()
            )
        return {"items": [dict(r) for r in rows]}

    @app.post("/v1/sessions", status_code=201)
    def new_session(body: CreateSession, user=user_dependency):
        return sessions.create(user["principal"], "demo", snapshot)

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: str, user=user_dependency):
        session = sessions.get(session_id, user["principal"])
        with repository.engine.connect() as c:
            ids = (
                c.execute(
                    text("SELECT id FROM sg_runs WHERE session=:s ORDER BY revision"),
                    {"s": session_id},
                )
                .scalars()
                .all()
            )
        return {
            **session,
            "messages": [
                public_run(sessions.run(r, user["principal"]), user["principal"])
                for r in ids
            ],
        }

    @app.post("/v1/sessions/{session_id}/product")
    def select_product(session_id: str, body: SelectProduct, user=user_dependency):
        return sessions.select(
            session_id,
            user["principal"],
            body.product_id,
            body.variant_id,
            body.expected_revision,
        )

    @app.post("/v1/sessions/{session_id}/messages", status_code=202)
    def message(session_id: str, body: Message, user=user_dependency):
        session = sessions.get(session_id, user["principal"])
        with repository.engine.connect() as c:
            prior = c.execute(
                text("SELECT id FROM sg_runs WHERE session=:s AND client_message=:m"),
                {"s": session_id, "m": body.client_message_id},
            ).scalar_one_or_none()
        if prior:
            record = sessions.submit(
                session_id,
                user["principal"],
                body.client_message_id,
                body.text,
                body.expected_session_revision,
                attachment_ids=body.attachment_ids,
                reply_to_step_id=body.reply_to_step_id,
            )
            return public_run(record, user["principal"])
        if len(set(body.attachment_ids)) != len(body.attachment_ids):
            raise ValueError("duplicate attachments")
        for uid in body.attachment_ids:
            uploads.record(uid, user["principal"], session_id, session["task"])
        if body.reply_to_step_id:
            with repository.engine.connect() as c:
                rows = (
                    c.execute(
                        text(
                            "SELECT result FROM sg_runs WHERE session=:s AND task=:t AND result IS NOT NULL"
                        ),
                        {"s": session_id, "t": session["task"]},
                    )
                    .scalars()
                    .all()
                )
            if not any(
                body.reply_to_step_id == step["step_id"]
                for raw in rows
                for step in json.loads(raw).get("answer", {}).get("steps", [])
            ):
                raise PermissionError("STEP_NOT_IN_CURRENT_TASK")
        # Retries of an accepted message stay valid even when the worker is temporarily unavailable.
        with repository.engine.connect() as c:
            exists = c.execute(
                text("SELECT id FROM sg_runs WHERE session=:s AND client_message=:m"),
                {"s": session_id, "m": body.client_message_id},
            ).scalar_one_or_none()
        if not exists and not worker.available():
            raise HTTPException(503, detail={"code": "MODEL_OR_QUEUE_UNAVAILABLE"})
        record = sessions.submit(
            session_id,
            user["principal"],
            body.client_message_id,
            body.text,
            body.expected_session_revision,
            attachment_ids=body.attachment_ids,
            reply_to_step_id=body.reply_to_step_id,
        )
        if not exists:
            sessions.event(record["id"], "run.accepted", {})
        if record["status"] not in TERMINAL:
            worker.schedule(record["id"], user["principal"])
        return public_run(
            sessions.run(record["id"], user["principal"]), user["principal"]
        )

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str, user=user_dependency):
        return public_run(sessions.run(run_id, user["principal"]), user["principal"])

    @app.post("/v1/runs/{run_id}/cancel")
    def cancel(run_id: str, user=user_dependency):
        record = sessions.cancel(run_id, user["principal"])
        sessions.event(
            run_id,
            "run.cancelled"
            if record["status"] == "CANCELLED"
            else "run.cancel_requested",
            {},
        )
        return public_run(record, user["principal"])

    @app.post("/v1/runs/{run_id}/resume", status_code=202)
    def resume(run_id: str, user=user_dependency):
        record = sessions.run(run_id, user["principal"])
        if record["status"] in ("QUEUED", "RUNNING"):
            worker.schedule(run_id, user["principal"])
        return public_run(record, user["principal"])

    @app.post("/v1/runs/{run_id}/confirm", status_code=202)
    def confirm(run_id: str, body: Confirm, user=user_dependency):
        sessions.run(run_id, user["principal"])
        with repository.engine.connect() as c:
            owner_run = c.execute(
                text("SELECT run FROM sg_confirmations WHERE id=:id"),
                {"id": body.confirmation_id},
            ).scalar_one_or_none()
        if owner_run != run_id:
            raise PermissionError("confirmation run mismatch")
        rid = SimulatedService(sessions).approve(
            body.confirmation_id,
            user["principal"],
            body.arguments_hash,
            body.expected_session_revision,
        )
        worker.schedule(rid, user["principal"])
        return public_run(sessions.run(rid, user["principal"]), user["principal"])

    @app.get("/v1/runs/{run_id}/events")
    async def events(run_id: str, request: Request, user=user_dependency):
        sessions.run(run_id, user["principal"])
        try:
            cursor = int(
                request.headers.get(
                    "last-event-id", request.query_params.get("after", "0")
                )
            )
        except ValueError:
            raise HTTPException(400, detail={"code": "INVALID_EVENT_CURSOR"}) from None
        if cursor < 0:
            raise HTTPException(400, detail={"code": "INVALID_EVENT_CURSOR"})

        async def stream():
            nonlocal cursor
            for _ in range(240):
                if await request.is_disconnected():
                    break
                rows = await run_in_threadpool(
                    sessions.events, run_id, user["principal"]
                )
                for event in rows:
                    if event["seq"] <= cursor:
                        continue
                    cursor = event["seq"]
                    payload = event["payload"]
                    label = PROGRESS.get(
                        payload.get("role")
                        or payload.get("name")
                        or payload.get("tool_name"),
                        "处理请求",
                    )
                    safe = {"seq": cursor, "type": event["kind"], "label": label}
                    yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(safe, ensure_ascii=False)}\n\n"
                record = await run_in_threadpool(
                    sessions.run, run_id, user["principal"]
                )
                if record["status"] in TERMINAL:
                    yield "event: finished\ndata: {}\n\n"
                    break
                yield ": keepalive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.get("/v1/assets/{asset_id}")
    def get_asset(asset_id: str, user=user_dependency):
        asset = repository.get(Asset, asset_id)
        if asset.scope.principal_id != user["principal"]:
            raise PermissionError("asset owner mismatch")
        return Response(assets.read(asset_id, asset.scope), media_type=asset.mime)

    @app.get("/v1/sources/{evidence_id}")
    def get_source(evidence_id: str, user=user_dependency):
        ev = repository.get(Evidence, evidence_id)
        if ev.scope.principal_id != user["principal"]:
            raise PermissionError("source owner mismatch")
        repository.authorize(ev.scope)
        Snapshots(repository).readable(ev.scope.snapshot_id)
        product = repository.get(Product, ev.scope.authorized_product_ids[0])
        locator = ev.source_locator
        if not isinstance(locator, ManualLocator):
            raise HTTPException(400, detail={"code": "SOURCE_FORMAT"})
        doc = repository.get(DocumentVersion, locator.doc_version_id)
        full = None
        if ev.asset_ids:
            asset = repository.get(Asset, ev.asset_ids[0])
            seen = set()
            while asset.transform.parent_asset_id:
                if asset.asset_id in seen:
                    raise ValueError("invalid asset lineage")
                seen.add(asset.asset_id)
                asset = repository.get(Asset, asset.transform.parent_asset_id)
            assets.read(asset.asset_id, ev.scope)
            full = asset.asset_id
        return {
            "evidence_id": evidence_id,
            "product": product.model,
            "page_index_0based": locator.page_index_0based,
            "page_label": locator.page_label,
            "version_label": doc.version_label,
            "text": ev.text[:12000],
            "text_truncated": len(ev.text) > 12000,
            "full_page_asset_id": full,
            "asset_ids": list(ev.asset_ids),
        }

    @app.post("/v1/uploads", status_code=201)
    async def upload(request: Request, session_id: str, user=user_dependency):
        sessions.get(session_id, user["principal"])
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_BYTES:
                raise HTTPException(413, detail={"code": "UPLOAD_SIZE"})
            content.extend(chunk)
        try:
            return await run_in_threadpool(
                uploads.add,
                user["principal"],
                session_id,
                bytes(content),
                request.headers.get("content-type", "").split(";")[0],
            )
        except (OSError, SyntaxError, ImageError):
            raise HTTPException(415, detail={"code": "UPLOAD_FORMAT"}) from None

    @app.get("/v1/uploads/{upload_id}")
    def get_upload(upload_id: str, user=user_dependency):
        return Response(
            uploads.read(upload_id, user["principal"]), media_type="image/png"
        )

    @app.post("/v1/feedback", status_code=201)
    def feedback(body: Feedback, user=user_dependency):
        sessions.run(body.run_id, user["principal"])
        identifier = new_id("feedback")
        with repository.engine.begin() as c:
            c.execute(
                text("INSERT INTO sg_feedback VALUES (:id,:r,:p,:why,:note)"),
                {
                    "id": identifier,
                    "r": body.run_id,
                    "p": user["principal"],
                    "why": body.reason,
                    "note": body.note,
                },
            )
        return {"feedback_id": identifier}

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        try:
            index_factory(snapshot).health()
        except (ValueError, OSError, KeyError):
            raise HTTPException(503, detail={"code": "INDEX_NOT_READY"}) from None
        if not worker.available():
            raise HTTPException(503, detail={"code": "MODEL_OR_QUEUE_UNAVAILABLE"})
        return {
            "status": "ready",
            "model_service": "observed_available"
            if worker.health["verified"]
            else "configured_not_probed",
        }

    if web_dist and (web_dist / "index.html").exists():
        app.mount(
            "/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets"
        )

        @app.get("/favicon.svg")
        def favicon():
            return FileResponse(web_dist / "favicon.svg", media_type="image/svg+xml")

        @app.get("/")
        def web():
            return FileResponse(web_dist / "index.html")

    return app


# Pillow raises this distinct exception before decoding oversized images.
from PIL.Image import DecompressionBombError as ImageError
