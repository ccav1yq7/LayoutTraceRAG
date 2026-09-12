"""Compare the production region wrapper with the exact pinned upstream function."""

import argparse
import contextlib
import io
import json
import warnings
from pathlib import Path

from shopguide.benchmarks.metrics import (
    region_metrics,
    upstream_region_function,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    reference, digest = upstream_region_function(args.source)
    cases = {
        "perfect": ([[1]], [[1]], [[1, 2]]),
        "empty": ([[]], [[1]], [[1, 2]]),
        "wrong": ([[2]], [[1]], [[1, 2]]),
        "duplicate_region": ([[1, 1]], [[1]], [[1, 2]]),
        "unknown_candidate": ([[99]], [[1]], [[1, 2]]),
        "wrong_page_partial_gold": ([[2]], [[1, 2]], [[2]]),
        "multiple_gold": ([[1]], [[1, 2]], [[1, 2, 3]]),
        "mixed_instances": (
            [[1, 2], [1], []],
            [[1], [1, 2], [1]],
            [[1, 2], [1, 2], [1, 2]],
        ),
    }
    results = {}
    for name, values in cases.items():
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            expected = reference(*values)
        actual = region_metrics(*values)
        if any(abs(expected[k] - v) > 1e-12 for k, v in actual.items()):
            raise RuntimeError("parity mismatch")
        results[name] = actual
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "status": "passed",
                "cases": results,
                "scorer_sha256": digest,
                "source_revision": "5226a9aa849fd3b8f35620e7b7d7d10c09d754a2",
                "official_benchmark": False,
                "text_parity": "not_run",
            },
            indent=2,
        )
        + "\n"
    )
    print(f"{len(cases)} region wrapper parity cases passed")


if __name__ == "__main__":
    main()
