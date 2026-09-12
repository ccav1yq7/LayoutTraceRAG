"""Metric compatibility, independently implemented from pinned upstream semantics."""

import ast
import hashlib
import subprocess
from pathlib import Path


def region_metrics(predictions, golds, candidates):
    if not len(predictions) == len(golds) == len(candidates) or not predictions:
        raise ValueError("metric row count mismatch/empty")
    counts = []
    for pred, gold, regions in zip(predictions, golds, candidates, strict=True):
        p = set(pred) & set(regions)
        g = set(gold) & set(regions)
        counts.append((len(p & g), len(p - g), len(g - p)))

    def score(tp, fp, fn):
        return [
            tp / (tp + fp) if tp + fp else 0.0,
            tp / (tp + fn) if tp + fn else 0.0,
            2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        ]

    per = [score(*c) for c in counts]
    macro = [sum(s[i] for s in per) / len(per) for i in range(3)]
    micro = score(*(sum(c[i] for c in counts) for i in range(3)))
    return dict(
        zip(
            (
                "instance_precision",
                "instance_recall",
                "instance_f1",
                "all_precision",
                "all_recall",
                "all_f1",
            ),
            macro + micro,
            strict=True,
        )
    )


def upstream_region_function(source: Path):
    # Executes the exact trusted function body only, without importing GPU/NLGEval.
    revision = "5226a9aa849fd3b8f35620e7b7d7d10c09d754a2"
    pinned = subprocess.check_output(
        ["git", "-C", str(source.parent), "show", revision + ":evaluate.py"]
    )
    raw = source.read_bytes()
    if raw != pinned:
        raise ValueError("upstream scorer source differs from pinned commit")
    from sklearn.metrics import f1_score, precision_score, recall_score

    tree = ast.parse(raw)
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "compute_visual_answer_metics"
    )
    namespace = {
        "precision_score": precision_score,
        "recall_score": recall_score,
        "f1_score": f1_score,
    }
    exec(  # noqa: S102 - SHA-locked official function
        compile(ast.Module(body=[fn], type_ignores=[]), str(source), "exec"), namespace
    )
    return namespace[fn.name], hashlib.sha256(raw).hexdigest()
