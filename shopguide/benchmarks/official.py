"""Formal metric bridge with a frozen schedule and separately isolated scorer."""

import hashlib
import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_evaluation(
    manifest_path, requests_path, predictions_path, private, corpus, predictions
):
    manifest = json.loads(manifest_path.read_text())
    files = {
        "requests_sha256": requests_path,
        "corpus_manifest_sha256": corpus / "manifest.json",
        "private_split_sha256": private / f"{manifest['split']}.jsonl",
    }
    if manifest.get("run_kind") != "real_baseline_validation":
        raise ValueError("REAL_EVALUATION_MANIFEST_REQUIRED")
    for key, path in files.items():
        if manifest.get(key) != sha(path):
            raise ValueError("EVALUATION_INPUT_CHANGED")
    if not manifest.get("model_version_policy"):
        raise ValueError("MODEL_VERSION_POLICY_REQUIRED")
    for prediction in predictions:
        if prediction.model_mode != "real":
            raise ValueError("FAKE_FORMAL_PREDICTION")
        if (
            prediction.protocol != manifest["protocol"]
            or prediction.split != manifest["split"]
            or prediction.baseline != manifest["baseline"]
            or prediction.snapshot_id != manifest["snapshot_id"]
        ):
            raise ValueError("EVALUATION_PROFILE_CHANGED")
        path = (
            predictions_path.parent
            / (predictions_path.name + ".runs")
            / (prediction.run_id + ".json")
        )
        record = json.loads(path.read_text())
        configuration = record.get("configuration")
        if not configuration:
            if prediction.status != "error" or prediction.model_calls:
                raise ValueError("MISSING_REAL_RUN_CONFIGURATION")
            continue
        digest = hashlib.sha256(
            json.dumps(configuration, sort_keys=True).encode()
        ).hexdigest()
        if (
            digest != prediction.configuration_sha256
            or configuration != manifest["configuration"]
        ):
            raise ValueError("RUN_CONFIGURATION_CHANGED")
        if (
            configuration["embedding"]["model_mode"] != "real"
            or configuration["reranker"]["model_mode"] != "real"
            or record.get("component_modes", {}).get("gateway") != "real"
        ):
            raise ValueError("FAKE_FORMAL_COMPONENT")
        answer = record["answer"]
        expected_status = (
            answer["status"]
            if record["status"] == "completed"
            else "abstained"
            if record["error"] == "WRITER_ABSTAINED"
            else "error"
        )
        if prediction.status != expected_status or prediction.error != record["error"]:
            raise ValueError("PREDICTION_STATUS_MISMATCH")
        expected_text = (
            "\n".join(
                [
                    answer["summary"],
                    *answer.get("prerequisites", []),
                    *(step["text"] for step in answer.get("steps", [])),
                ]
            )
            if record["status"] == "completed"
            else ""
        )
        expected_regions = list(
            dict.fromkeys(
                c["source_locator"]["region_id"]
                for c in answer.get("citations", [])
                if c["source_locator"].get("region_id")
            )
        )
        expected_assets = list(
            dict.fromkeys(
                a for step in answer.get("steps", []) for a in step["display_asset_ids"]
            )
        )
        if (
            prediction.answer_text != expected_text
            or list(prediction.predicted_region_ids) != expected_regions
            or list(prediction.display_asset_ids) != expected_assets
        ):
            raise ValueError("PREDICTION_RUN_MISMATCH")
        for name in ("retrieved_page_ids", "visited_page_ids"):
            if list(getattr(prediction, name)) != record[name]:
                raise ValueError("PREDICTION_RUN_MISMATCH")
        if (
            prediction.model_calls != record["usage"]["model_calls"]
            or prediction.image_inputs != record["usage"]["image_inputs"]
        ):
            raise ValueError("PREDICTION_USAGE_MISMATCH")
        # Bind scored text/selections to the actual persisted inference result.
        if record.get("run_id") != prediction.run_id:
            raise ValueError("RUN_ID_MISMATCH")
    return manifest


def run_reference(items, *, python, source, lock, timeout=600, java_home=None):
    worker = Path(__file__).with_name("reference_worker.py")
    with tempfile.TemporaryDirectory(prefix="pm209-score-") as directory:
        root = Path(directory)
        input_path, output_path = root / "input.json", root / "output.json"
        input_path.write_text(json.dumps(items))
        # No model credentials are passed to the reference process.
        env = {
            k: v
            for k, v in os.environ.items()
            if k in {"PATH", "HOME", "LANG", "JAVA_HOME", "HF_HOME", "HF_HUB_CACHE"}
        }
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        java_home = java_home or (
            Path(env["JAVA_HOME"]) if "JAVA_HOME" in env else None
        )
        if java_home is None:
            raise ValueError("PINNED_REFERENCE_JAVA_HOME_REQUIRED")
        env["JAVA_HOME"] = str(java_home.resolve())
        env["PATH"] = (
            str(java_home.resolve() / "bin") + os.pathsep + env.get("PATH", "")
        )
        with (root / "log.txt").open("w+") as log:
            process = subprocess.Popen(
                [
                    str(python.absolute()),
                    "-I",
                    str(worker),
                    "--source",
                    str(source.resolve()),
                    "--lock",
                    str(lock.resolve()),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise RuntimeError("REFERENCE_TIMEOUT") from None
            finally:
                # Also reap Java children if the Python worker failed prematurely.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if code:
                log.seek(0)
                raise RuntimeError("REFERENCE_FAILED: " + log.read()[-4000:])
        return json.loads(output_path.read_text())
