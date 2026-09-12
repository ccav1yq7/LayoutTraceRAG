"""Run exact pinned upstream region function AST without importing its GPU stack.

This is isolated function parity on synthetic vectors, not full MPMQA evaluation.
"""

import argparse
import ast
import contextlib
import hashlib
import io
import json
import math
import warnings
from pathlib import Path

from sklearn.metrics import f1_score, precision_score, recall_score


def region_reference(predictions, golds, candidates):
    counts = []
    for pred, gold, regions in zip(predictions, golds, candidates, strict=True):
        p = set(pred) & set(regions)
        g = set(gold) & set(regions)
        counts.append((len(p & g), len(p - g), len(g - p)))

    def scores(tp, fp, fn):
        return (
            tp / (tp + fp) if tp + fp else 0,
            tp / (tp + fn) if tp + fn else 0,
            2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
        )

    instance = [scores(*x) for x in counts]
    micro = scores(*(sum(x[i] for x in counts) for i in range(3)))
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
            [sum(x[i] for x in instance) / len(instance) for i in range(3)]
            + list(micro),
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.read_bytes()
    expected = "5226a9aa849fd3b8f35620e7b7d7d10c09d754a2"
    import subprocess

    revision = subprocess.check_output(
        ["git", "-C", str(args.source.parent), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != expected:
        raise SystemExit("BLOCKED: unexpected upstream revision")
    pinned = subprocess.check_output(
        ["git", "-C", str(args.source.parent), "show", expected + ":evaluate.py"]
    )
    if source != pinned:
        raise SystemExit("BLOCKED: upstream scorer was modified")
    tree = ast.parse(source)
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "compute_visual_answer_metics"
    )
    namespace = {
        "precision_score": precision_score,
        "recall_score": recall_score,
        "f1_score": f1_score,
    }
    exec(  # noqa: S102 - exact SHA-locked upstream function, no imported modules
        compile(ast.Module(body=[function], type_ignores=[]), str(args.source), "exec"),
        namespace,
    )
    cases = {
        "perfect": ([[1]], [[1]], [[1, 2]]),
        "empty": ([[]], [[1]], [[1, 2]]),
        "wrong_page": ([[3]], [[1]], [[1, 2]]),
        "duplicates": ([[1, 1]], [[1]], [[1, 2]]),
        "mixed": ([[1, 2], [2], []], [[1], [1, 2], [1]], [[1, 2], [1, 2, 3], [1, 2]]),
    }
    results = {}
    for name, (pred, gold, regions) in cases.items():
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            actual = namespace[function.name](pred, gold, regions)
        expected_result = region_reference(pred, gold, regions)
        assert all(
            math.isclose(actual[k], v, abs_tol=1e-12)
            for k, v in expected_result.items()
        )
        results[name] = actual
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "status": "passed",
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "source_revision": revision,
                "method": "exact function AST; no body changes",
                "cases": results,
                "full_text_scorer": "BLOCKED: NLGEval resources and isolated reference runtime not provisioned",
                "official_benchmark": False,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"{len(cases)} synthetic upstream region parity cases passed")


if __name__ == "__main__":
    main()
