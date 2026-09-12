import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text

from shopguide.api.app import create_app
from shopguide.api.demo import WebDemoGateway, seed_demo
from shopguide.retrieval.models import HashEmbedder, RRFReranker
from shopguide.retrieval.store import ScopedIndex
from shopguide.schemas import Asset
from shopguide.storage.repository import Repository


@pytest.fixture
def web_api(tmp_path):
    repo = Repository(tmp_path / "metadata.db")
    snapshot = seed_demo(repo, tmp_path)

    def index(s):
        return ScopedIndex(
            repo, tmp_path / "assets", tmp_path / "indexes", s, HashEmbedder()
        )

    app = create_app(
        repo,
        tmp_path,
        index,
        RRFReranker(),
        WebDemoGateway,
        demo=True,
        snapshot=snapshot,
    )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
    ) as client:
        login = client.post("/v1/auth/demo")
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        other, _csrf = app.state.auth.issue("user_other")
        yield repo, app, client, {"Authorization": "Bearer " + other}, tmp_path
    repo.close()


def session(client, select=True):
    s = client.post("/v1/sessions", json={}).json()
    if select:
        r = client.post(
            "/v1/sessions/" + s["id"] + "/product",
            json={
                "product_id": "product_web_a",
                "variant_id": "variant_web_a",
                "expected_revision": s["revision"],
            },
        )
        assert r.status_code == 200
        s = r.json()
    return s


def send(client, s, question="怎样启动？", message_id="message_api", **extras):
    return client.post(
        "/v1/sessions/" + s["id"] + "/messages",
        json={
            "client_message_id": message_id,
            "expected_session_revision": s["revision"],
            "text": question,
            **extras,
        },
    )


def finish(client, rid):
    for _ in range(160):
        result = client.get("/v1/runs/" + rid).json()
        if result["status"] not in ("QUEUED", "RUNNING"):
            return result
        time.sleep(0.04)
    raise AssertionError("worker did not finish")


def png():
    out = io.BytesIO()
    Image.new("RGB", (90, 60), "red").save(out, format="PNG")
    return out.getvalue()


def test_auth_csrf_origin_and_object_isolation(web_api):
    _repo, _app, client, other, _root = web_api
    assert len(client.get("/v1/catalog/purchases").json()["items"]) == 3
    assert len(client.get("/v1/catalog/purchases", headers=other).json()["items"]) == 2
    assert (
        client.post(
            "/v1/sessions", json={}, headers={"X-CSRF-Token": "bad"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/auth/demo", headers={"Origin": "https://evil.invalid"}
        ).status_code
        == 403
    )
    assert (
        client.post("/v1/auth/demo", headers={"Host": "evil.invalid"}).status_code
        == 400
    )
    s = session(client)
    assert client.get("/v1/sessions/" + s["id"], headers=other).status_code == 404
    assert (
        client.post("/v1/sessions", json={"principal_id": "user_other"}).status_code
        == 422
    )
    client.cookies.clear()
    assert client.get("/v1/catalog/purchases").status_code == 401


def test_api_answer_replay_sse_and_revocation(web_api):
    repo, _app, client, other, _root = web_api
    s = session(client)
    response = send(client, s)
    assert response.status_code == 202
    rid = response.json()["run_id"]
    done = finish(client, rid)
    assert done["status"] == "COMPLETED", done
    replay = send(client, s)
    assert replay.status_code == 202 and replay.json()["run_id"] == rid
    assert send(client, s, "changed text").status_code == 409
    assert client.get("/v1/runs/" + rid, headers=other).status_code == 404
    events = client.get("/v1/runs/" + rid + "/events")
    assert events.status_code == 200
    ids = [
        int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")
    ]
    assert ids == sorted(set(ids)) and ids
    assert "request_sha256" not in events.text and "candidate_draft" not in events.text
    later = client.get(
        "/v1/runs/" + rid + "/events", headers={"Last-Event-ID": str(ids[-1])}
    )
    assert "id: " not in later.text
    assert client.get("/v1/runs/" + rid + "/events", headers=other).status_code == 404
    assert (
        client.get(
            "/v1/runs/" + rid + "/events", headers={"Last-Event-ID": "-1"}
        ).status_code
        == 400
    )
    aid = done["result"]["answer"]["steps"][0]["display_asset_ids"][0]
    image = client.get("/v1/assets/" + aid)
    assert image.status_code == 200 and image.headers["cache-control"] == "no-store"
    assert client.get("/v1/assets/" + aid, headers=other).status_code == 404
    citation = done["result"]["answer"]["citations"][0]["evidence_id"]
    source = client.get("/v1/sources/" + citation)
    assert source.status_code == 200 and source.json()["full_page_asset_id"]
    repo.revoke(repo.get(Asset, aid).source_locator.doc_version_id)
    assert client.get("/v1/assets/" + aid).status_code == 404
    withheld = client.get("/v1/runs/" + rid).json()
    assert (
        withheld["result"]["status"] == "source_unavailable"
        and "answer" not in withheld["result"]
    )
    assert (
        client.get("/v1/sessions/" + s["id"]).json()["messages"][0]["result"]["status"]
        == "source_unavailable"
    )


def test_upload_limits_ownership_binding_and_inspection(web_api):
    _repo, _app, client, other, _root = web_api
    s = session(client)
    bad = client.post(
        "/v1/uploads",
        params={"session_id": s["id"]},
        content=b"<svg/>",
        headers={"Content-Type": "image/svg+xml"},
    )
    assert bad.status_code == 415
    mismatch = client.post(
        "/v1/uploads",
        params={"session_id": s["id"]},
        content=png(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert mismatch.status_code == 415
    uploaded = client.post(
        "/v1/uploads",
        params={"session_id": s["id"]},
        content=png(),
        headers={"Content-Type": "image/png", "X-Filename": "../../escape.png"},
    )
    assert uploaded.status_code == 201
    uid = uploaded.json()["upload_id"]
    data = client.get("/v1/uploads/" + uid)
    assert data.status_code == 200
    with Image.open(io.BytesIO(data.content)) as image:
        assert not image.getexif()
    assert client.get("/v1/uploads/" + uid, headers=other).status_code == 404
    other_session = session(client)
    assert send(client, other_session, attachment_ids=[uid]).status_code == 404
    result = send(client, s, attachment_ids=[uid])
    assert result.status_code == 202
    done = finish(client, result.json()["run_id"])
    assert done["status"] == "COMPLETED", done
    assert done["result"]["used_upload_ids"] == [uid]
    assert done["result"]["usage"]["image_inputs"] == 6
    assert all(
        a.startswith("asset_")
        for step in done["result"]["answer"]["steps"]
        for a in step["display_asset_ids"]
    )
    events = client.get("/v1/runs/" + done["run_id"] + "/events").text
    assert "查看上传图片" in events
    current = client.get("/v1/sessions/" + s["id"]).json()
    switched = client.post(
        "/v1/sessions/" + s["id"] + "/product",
        json={
            "product_id": "product_web_b",
            "variant_id": "variant_web_b",
            "expected_revision": current["revision"],
        },
    ).json()
    assert (
        send(
            client, switched, message_id="message_changed", attachment_ids=[uid]
        ).status_code
        == 404
    )
    assert send(client, s, attachment_ids=[uid]).json()["run_id"] == done["run_id"]


def test_step_reply_confirmation_and_feedback(web_api):
    repo, _app, client, other, _root = web_api
    s = session(client)
    first = finish(client, send(client, s).json()["run_id"])
    step = first["result"]["answer"]["steps"][0]["step_id"]
    current = client.get("/v1/sessions/" + s["id"]).json()
    reply = send(
        client, current, "第二步在哪里？", "message_reply", reply_to_step_id=step
    )
    assert reply.status_code == 202
    finish(client, reply.json()["run_id"])
    assert (
        client.post(
            "/v1/feedback",
            json={"run_id": first["run_id"], "reason": "image", "note": "fixture"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/v1/feedback",
            json={"run_id": first["run_id"], "reason": "image"},
            headers=other,
        ).status_code
        == 404
    )
    current = client.get("/v1/sessions/" + s["id"]).json()
    prepared = finish(
        client,
        send(client, current, "请准备模拟售后工单", "message_ticket").json()["run_id"],
    )
    assert prepared["status"] == "WAITING_CONFIRMATION", prepared
    proposal = prepared["result"]
    body = {
        "confirmation_id": proposal["confirmation_id"],
        "arguments_hash": proposal["arguments_hash"],
        "expected_session_revision": prepared["revision"],
    }
    assert (
        client.post(
            "/v1/runs/" + prepared["run_id"] + "/confirm", json=body, headers=other
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/v1/runs/" + prepared["run_id"] + "/confirm", json=body
        ).status_code
        == 202
    )
    committed = finish(client, prepared["run_id"])
    assert committed["result"]["service_request"]["simulated"]
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM sg_tickets")).scalar_one() == 1


def test_malformed_size_and_pixel_limits(web_api):
    import base64
    import struct
    import zlib

    repo, _app, client, _other, _root = web_api
    s = session(client)
    broken = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j8S8AAAAASUVORK5CYII="
    )
    assert (
        client.post(
            "/v1/uploads",
            params={"session_id": s["id"]},
            content=broken,
            headers={"Content-Type": "image/png"},
        ).status_code
        == 415
    )
    assert (
        client.post(
            "/v1/uploads",
            params={"session_id": s["id"]},
            content=b"x" * (20 * 1024 * 1024 + 1),
            headers={"Content-Type": "image/png"},
        ).status_code
        == 413
    )

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    huge = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 5001, 5000, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )
    assert (
        client.post(
            "/v1/uploads",
            params={"session_id": s["id"]},
            content=huge,
            headers={"Content-Type": "image/png"},
        ).status_code
        == 413
    )
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM sg_uploads")).scalar_one() == 0


def test_local_bootstrap_rejects_remote_and_forwarded_identity(tmp_path):
    repo = Repository(tmp_path / "db")
    snapshot = seed_demo(repo, tmp_path)

    def index(s):
        return ScopedIndex(
            repo, tmp_path / "assets", tmp_path / "indexes", s, HashEmbedder()
        )

    app = create_app(
        repo,
        tmp_path,
        index,
        RRFReranker(),
        WebDemoGateway,
        demo=True,
        snapshot=snapshot,
        worker_enabled=False,
    )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("198.51.100.10", 4000)
    ) as client:
        assert (
            client.post(
                "/v1/auth/demo", headers={"X-Forwarded-For": "127.0.0.1"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/v1/me",
                headers={
                    "oai-authenticated-user-id": "user_demo",
                    "X-User-ID": "user_demo",
                },
            ).status_code
            == 401
        )
    repo.close()


def test_queued_cancel_and_resume_do_not_duplicate_messages(web_api):
    _repo, app, client, _other, _root = web_api
    app.state.worker.enabled = False
    s = session(client)
    accepted = send(client, s)
    rid = accepted.json()["run_id"]
    assert accepted.status_code == 202 and accepted.json()["status"] == "QUEUED"
    cancelled = client.post("/v1/runs/" + rid + "/cancel", json={})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    resumed = client.post("/v1/runs/" + rid + "/resume", json={})
    assert resumed.json()["status"] == "CANCELLED"
    assert send(client, s).json()["run_id"] == rid


def test_worker_model_failure_updates_readiness(tmp_path):
    class DownGateway(WebDemoGateway):
        def complete(self, *args):
            raise RuntimeError("MODEL_HTTP_503")

    repo = Repository(tmp_path / "db")
    snapshot = seed_demo(repo, tmp_path)

    def index(s):
        return ScopedIndex(
            repo, tmp_path / "assets", tmp_path / "indexes", s, HashEmbedder()
        )

    app = create_app(
        repo, tmp_path, index, RRFReranker(), DownGateway, demo=True, snapshot=snapshot
    )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 4000)
    ) as client:
        login = client.post("/v1/auth/demo")
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        s = session(client)
        run = finish(client, send(client, s).json()["run_id"])
        assert run["status"] == "FAILED"
        assert client.get("/health/ready").status_code == 503
        current = client.get("/v1/sessions/" + s["id"]).json()
        assert send(client, current, message_id="message_again").status_code == 503
    repo.close()
