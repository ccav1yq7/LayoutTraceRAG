"""No paid models: five-client load, SIGKILL recovery, cold backup and restored API."""

import argparse
import concurrent.futures
import json
import math
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from shopguide.ops.backup import backup, restore


@contextmanager
def client(port):
    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False
    ) as c:
        response = c.post("/v1/auth/demo")
        response.raise_for_status()
        c.headers["X-CSRF-Token"] = response.json()["csrf"]
        yield c


def create(c):
    session = c.post("/v1/sessions", json={}).json()
    response = c.post(
        "/v1/sessions/" + session["id"] + "/product",
        json={
            "product_id": "product_web_a",
            "variant_id": "variant_web_a",
            "expected_revision": session["revision"],
        },
    )
    response.raise_for_status()
    return response.json()


def finish(c, rid):
    for _ in range(300):
        response = c.get("/v1/runs/" + rid)
        response.raise_for_status()
        value = response.json()
        if value["status"] not in ("QUEUED", "RUNNING"):
            assert value["status"] == "COMPLETED", value["status"]
            assert value["result"]["answer"]["steps"]
            return value
        time.sleep(0.1)
    raise RuntimeError("DRILL_RUN_TIMEOUT")


def start(root, port, log):
    process = subprocess.Popen(
        [
            sys.executable,
            "scripts/shopguide/browser_test_server.py",
            "--root",
            str(root),
            "--port",
            str(port),
        ],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("DRILL_SERVER_EXITED")
            try:
                if (
                    httpx.get(
                        f"http://127.0.0.1:{port}/health/ready",
                        timeout=1,
                        trust_env=False,
                    ).status_code
                    == 200
                ):
                    return process
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        raise RuntimeError("DRILL_START_TIMEOUT")
    except BaseException:
        stop(process)
        raise


def stop(process, crash=False):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL if crash else signal.SIGTERM)
        process.wait(timeout=30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8487)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    root = args.out / "data"
    process = None
    with (args.out / "server.log").open("w") as log:
        try:
            process = start(root, args.port, log)

            def ask(i):
                with client(args.port) as c:
                    s = create(c)
                    before = time.monotonic()
                    response = c.post(
                        "/v1/sessions/" + s["id"] + "/messages",
                        json={
                            "client_message_id": f"message_load_{i}",
                            "expected_session_revision": s["revision"],
                            "text": "请说明启动方法",
                        },
                    )
                    admitted = time.monotonic() - before
                    if response.status_code == 429:
                        assert response.json()["detail"]["code"] == "RATE_LIMITED"
                        return {
                            "admission_seconds": admitted,
                            "completion_seconds": None,
                            "rate_limited": True,
                        }
                    response.raise_for_status()
                    finish(c, response.json()["run_id"])
                    return {
                        "admission_seconds": admitted,
                        "completion_seconds": time.monotonic() - before,
                        "rate_limited": False,
                    }

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                load = list(pool.map(ask, range(5)))
            with client(args.port) as c:
                session = create(c)
                body = {
                    "client_message_id": "message_crash",
                    "expected_session_revision": session["revision"],
                    "text": "请查看按钮原图再说明启动方法",
                }
                response = c.post(
                    "/v1/sessions/" + session["id"] + "/messages", json=body
                )
                response.raise_for_status()
                rid = response.json()["run_id"]
                before_crash = c.get("/v1/runs/" + rid).json()["status"]
                assert before_crash in ("QUEUED", "RUNNING")
                stop(process, crash=True)
                process = start(root, args.port, log)
                replay = c.post(
                    "/v1/sessions/" + session["id"] + "/messages", json=body
                )
                replay.raise_for_status()
                assert replay.json()["run_id"] == rid
                finished = finish(c, rid)
                assert (
                    len(c.get("/v1/sessions/" + session["id"]).json()["messages"]) == 1
                )
            stop(process)
            process = None
            saved = backup(root, args.out / "backup")
            recovered = restore(
                args.out / "backup",
                args.out / "restored",
                expected_sha=saved["manifest_sha256"],
            )
            process = start(args.out / "restored", args.port, log)
            with client(args.port) as c:
                after = c.get("/v1/runs/" + rid).json()
                assert after["result"]["answer"] == finished["result"]["answer"]
                aid = after["result"]["answer"]["steps"][0]["display_asset_ids"][0]
                image = c.get("/v1/assets/" + aid)
                image.raise_for_status()
                assert image.headers["content-type"].startswith("image/")
            accepted = [r for r in load if not r["rate_limited"]]
            assert len(accepted) == 2
            p95 = {
                name: sorted(r[name] for r in accepted)[
                    math.ceil(0.95 * len(accepted)) - 1
                ]
                for name in ("admission_seconds", "completion_seconds")
            }
            report = {
                "status": "pass",
                "model_mode": "delayed_fake",
                "clients": 5,
                "principals": 1,
                "accepted": len(accepted),
                "rate_limited": len(load) - len(accepted),
                "requests": load,
                "p95": p95,
                "real_model_performance": False,
                "crash_before": before_crash,
                "sigkill_recovery": True,
                "replay_same_run": True,
                "restored_answer_and_source": True,
                "backup": saved,
                "restore": recovered,
            }
            (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report))
        finally:
            stop(process)


if __name__ == "__main__":
    main()
