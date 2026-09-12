"""Process-group watchdog and immutable campaign schedules (sequential trials)."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .scoring import summarize, write_json
from .upstream import digest, tasks, verify

METHODS = ("fixed-one-batch", "native-react")


def freeze(root, out, *, num_trials=3, split="dev", task_ids=None):
    if out.exists():
        raise ValueError("MANIFEST_ALREADY_EXISTS")
    if not 1 <= num_trials <= 8 or split not in ("dev", "holdout", "all"):
        raise ValueError("INVALID_CAMPAIGN")
    lock = verify(root)
    all_tasks = tasks(root)
    count = len(all_tasks)
    dev = list(range(min(3, count)))
    ids = (
        dev
        if split == "dev"
        else list(range(3, count))
        if split == "holdout"
        else list(range(count))
    )
    if task_ids is not None:
        if (
            not task_ids
            or len(set(task_ids)) != len(task_ids)
            or not set(task_ids) <= set(ids)
        ):
            raise ValueError("TASK_SUBSET_OUTSIDE_REGISTERED_SPLIT")
        ids = list(task_ids)
    manifest = {
        "schema_version": 1,
        "profile": "ecom-deepseek-protocol-adaptation-v1",
        "upstream": lock,
        "task_count": count,
        "task_image_dependencies": {
            str(i): sorted(
                {
                    digest(url.encode())
                    for url in re.findall(
                        r"https?://[^\s<>\"\x27]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s<>\"\x27]*)?",
                        task.instruction,
                        re.IGNORECASE,
                    )
                }
            )
            for i, task in enumerate(all_tasks)
        },
        "image_readiness": "external audit required; unavailable tasks remain in denominator",
        "dev_ids": dev,
        "holdout_ids": list(range(3, count)),
        "split": split,
        "task_ids": ids,
        "num_trials": num_trials,
        "methods": list(METHODS),
        "model": "deepseek-v4-flash-vision-exp",
        "model_revision": None,
        "model_roles": ["agent", "user", "vision"],
        "agent_temperature": 0,
        "user_temperature": 0.3,
        "max_output_tokens": 2048,
        "max_turns": 20,
        "watchdog_seconds": 600,
        "native_time_metric_limit": 30,
        "max_tool_calls": 64,
        "max_agent_rounds_per_turn": 16,
        "seed": 20260909,
        "response_cache": "disabled locally; provider cache not controlled",
        "official_benchmark": False,
        "requires_budget_approval": True,
        "compatibility": [
            "SHA-verified definition loading avoids unused legacy model constructors",
            "UserBased call/policy unchanged; no-tool LLM transport replaced by DeepSeek",
            "Native vision function/template unchanged; image transport replaced by DeepSeek",
            "Bounded empty-response failure and process-group watchdog applied to both methods",
            "fixed-one-batch is a native tool-round ablation, not ShopGuide B2 or paper baseline",
        ],
    }
    # Freeze our adapter bytes too, including scorer bridge, transport and protocol limits.
    manifest["adapter_files"] = {
        p.name: digest(p.read_bytes())
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, manifest)
    out.with_suffix(out.suffix + ".sha256").write_text(digest(out.read_bytes()) + "\n")
    return {
        "manifest_sha256": digest(out.read_bytes()),
        "tasks": len(ids),
        "scheduled_trials": len(ids) * num_trials * len(METHODS),
    }


def supervise(request, *, timeout, python=sys.executable):
    out = Path(request["out"])
    out.mkdir(parents=True, exist_ok=False)
    request_file = out / "request.json"
    write_json(request_file, request)
    started = time.monotonic()
    with (out / "worker.log").open("w") as log:
        process = subprocess.Popen(
            [str(python), "-m", "shopguide.ecom.trial", str(request_file)],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        finally:
            # Includes MCP descendants, even if the worker exited first.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    result_file = out / "result.json"
    if result_file.exists():
        result = json.loads(result_file.read_text())
    else:
        result = {k: request[k] for k in ("task_id", "trial", "method")}
        result.update(
            status="timeout"
            if time.monotonic() - started >= timeout
            else "worker_failed",
            reward=0,
            action=0,
            search=0,
            output=0,
            official_benchmark=False,
        )
        data = out / "data"
        if data.exists() and not (out / "final-data").exists():
            shutil.copytree(data, out / "final-data")
        write_json(result_file, result)
    shutil.rmtree(out / "data", ignore_errors=True)
    return result


def run_campaign(
    root, manifest_path, out, config, max_model_calls, *, python=sys.executable
):
    if manifest_path.with_suffix(
        manifest_path.suffix + ".sha256"
    ).read_text().strip() != digest(manifest_path.read_bytes()):
        raise ValueError("CAMPAIGN_MANIFEST_DRIFT")
    manifest = json.loads(manifest_path.read_text())
    if verify(root) != manifest["upstream"]:
        raise ValueError("CAMPAIGN_SOURCE_DRIFT")
    current = {
        p.name: digest(p.read_bytes())
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    if current != manifest["adapter_files"]:
        raise ValueError("CAMPAIGN_ADAPTER_DRIFT")
    from ..models.providers import gateway_from_config

    gateway = gateway_from_config(config)
    if gateway.provider != "deepseek" or gateway.model_id != manifest["model"]:
        raise ValueError("CAMPAIGN_MODEL_DRIFT")
    if not 1 <= max_model_calls <= 100000:
        raise ValueError("EXPLICIT_MODEL_BUDGET_REQUIRED")
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(manifest_path, out / "manifest.json")
    budget = out / "budget.json"
    write_json(budget, {"limit": max_model_calls, "calls": 0, "reported_tokens": 0})
    rows = []
    for task in manifest["task_ids"]:
        for trial in range(manifest["num_trials"]):
            for method in manifest["methods"]:
                trial_out = out / f"{method}-task{task:03d}-trial{trial:02d}"
                request = {
                    "upstream": str(root.resolve()),
                    "out": str(trial_out.resolve()),
                    "method": method,
                    "task_id": task,
                    "trial": trial,
                    "private_config": str(config.resolve()),
                    "budget": str(budget.resolve()),
                }
                if json.loads(budget.read_text())["calls"] >= max_model_calls:
                    row = {
                        "method": method,
                        "task_id": task,
                        "trial": trial,
                        "status": "budget_not_run",
                        "reward": 0,
                        "action": 0,
                        "search": 0,
                        "output": 0,
                    }
                    trial_out.mkdir()
                    write_json(trial_out / "result.json", row)
                else:
                    row = supervise(
                        request, timeout=manifest["watchdog_seconds"], python=python
                    )
                rows.append(row)
                write_json(out / "report.json", summarize(manifest, rows))
    return summarize(manifest, rows)
