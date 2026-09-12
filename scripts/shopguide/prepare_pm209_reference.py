"""Provision only SHA-locked resources into an already installed reference env.

Run with .venv-pm209-reference/bin/python after installing the pinned NLGEval
checkout with --no-deps and syncing configs/pm209/requirements.lock.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    for name in (
        "lock",
        "source",
        "nlgeval-source",
        "stanford-archive",
        "java-archive",
        "java-home",
    ):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    lock = json.loads(a.lock.read_text())
    for root, revision in (
        (a.source, lock["upstream_revision"]),
        (a.nlgeval_source, lock["nlgeval_revision"]),
    ):
        if (
            subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            != revision
        ):
            raise ValueError("REFERENCE_REVISION_CHANGED")
    for relative, digest in lock["source_files"].items():
        if sha(a.source / relative) != digest:
            raise ValueError("REFERENCE_SOURCE_CHANGED")
    if (
        sha(a.stanford_archive) != lock["resource_archive_sha256"]
        or sha(a.java_archive) != lock["java"]["archive_sha256"]
    ):
        raise ValueError("REFERENCE_ARCHIVE_CHANGED")
    import nlgeval

    package = Path(nlgeval.__file__).parent
    with zipfile.ZipFile(a.stanford_archive) as z:
        for name in ("stanford-corenlp-3.6.0.jar", "stanford-corenlp-3.6.0-models.jar"):
            relative = "pycocoevalcap/spice/lib/" + name
            payload = z.read("stanford-corenlp-full-2015-12-09/" + name)
            if hashlib.sha256(payload).hexdigest() != lock["nlgeval_files"][relative]:
                raise ValueError("STANFORD_JAR_CHANGED")
            (package / relative).write_bytes(payload)
    for relative, digest in lock["nlgeval_files"].items():
        if sha(package / relative) != digest:
            raise ValueError("NLGEVAL_SOURCE_OR_RESOURCE_CHANGED")
    if not a.java_home.exists():
        a.java_home.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=a.java_home.parent) as temporary:
            with tarfile.open(a.java_archive) as t:
                t.extractall(temporary, filter="data")
            roots = list(Path(temporary).iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise ValueError("JAVA_ARCHIVE_LAYOUT_CHANGED")
            shutil.move(str(roots[0]), a.java_home)
    for relative, digest in lock["java"]["files"].items():
        if sha(a.java_home / relative) != digest:
            raise ValueError("REFERENCE_JAVA_CHANGED")
    from huggingface_hub import snapshot_download

    info = lock["tokenizer"]
    path = Path(
        snapshot_download(
            info["model_id"],
            revision=info["revision"],
            allow_patterns=list(info["files"]),
        )
    )
    for relative, digest in info["files"].items():
        if sha(path / relative) != digest:
            raise ValueError("REFERENCE_TOKENIZER_CHANGED")
    print(
        json.dumps(
            {
                "status": "provisioned",
                "lock_sha256": sha(a.lock),
                "metric_validation": "run pm209_reference_parity.py separately",
            }
        )
    )


if __name__ == "__main__":
    main()
