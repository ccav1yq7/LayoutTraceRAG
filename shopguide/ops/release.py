"""Evidence-presence gate, never substitutes for official benchmark execution."""

import json
from pathlib import Path

from .backup import sha

REQUIRED = (
    "python_regression",
    "browser_regression",
    "security",
    "restore_drill",
    "performance",
    "container",
    "dependency_audit",
    "licenses",
    "pm209_official",
    "ecom_fair_comparison",
    "production_auth",
    "image_provenance",
)


def release_check(checklist: Path, base: Path):
    document = json.loads(checklist.read_text())
    supplied = document["gates"]
    if set(supplied) - set(REQUIRED):
        raise ValueError("UNKNOWN_RELEASE_GATE")
    results = {}
    for gate in REQUIRED:
        entry = supplied.get(gate, {})
        paths = entry.get("evidence", [])
        valid = entry.get("status") == "pass" and bool(paths)
        for record in paths:
            path = (base / record["path"]).resolve()
            if (
                not path.is_relative_to(base.resolve())
                or not path.is_file()
                or sha(path) != record["sha256"]
            ):
                valid = False
        results[gate] = {
            "status": "pass" if valid else "blocked",
            "note": entry.get("note", "missing evidence"),
        }
    return {
        "decision": "eligible_for_manual_review"
        if all(g["status"] == "pass" for g in results.values())
        else "do_not_release",
        "automatic_deployment": False,
        "evidence_check_only": True,
        "gates": results,
    }
