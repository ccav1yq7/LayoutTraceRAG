"""Execute the locked full text/region scorer on synthetic edge cases, no models."""

import argparse
import json
from pathlib import Path

from shopguide.benchmarks.metrics import region_metrics
from shopguide.benchmarks.official import run_reference, sha


def main():
    p = argparse.ArgumentParser()
    for name in ("python", "source", "lock", "java-home", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    cases = {
        "perfect_punctuation_case": [
            {
                "caption": "PRESS the blue button .",
                "gt": "Press the blue button .",
                "pred_regions": ["r1"],
                "gt_regions": ["r1"],
                "all_regions": ["r1", "r2"],
                "gt_region_cls": ["Text"],
                "all_region_cls": ["Text", "Text"],
            },
            {
                "caption": "Turn the volume dial .",
                "gt": "Turn the volume dial .",
                "pred_regions": ["r2"],
                "gt_regions": ["r2"],
                "all_regions": ["r1", "r2"],
                "gt_region_cls": ["Text"],
                "all_region_cls": ["Text", "Text"],
            },
        ],
        "all_empty_predictions": [
            {
                "caption": "",
                "gt": "Press the blue button .",
                "pred_regions": [],
                "gt_regions": ["r1"],
                "all_regions": ["r1", "r2"],
                "gt_region_cls": ["Text"],
                "all_region_cls": ["Text", "Text"],
            },
            {
                "caption": "",
                "gt": "Turn the volume dial .",
                "pred_regions": [],
                "gt_regions": ["r2"],
                "all_regions": ["r1", "r2"],
                "gt_region_cls": ["Text"],
                "all_region_cls": ["Text", "Text"],
            },
        ],
    }
    cases["all_six_region_types"] = [
        dict(
            cases["perfect_punctuation_case"][i % 2],
            gt_region_cls=[kind],
            all_region_cls=[kind, kind],
        )
        for i, kind in enumerate(
            ("Text", "Title", "Product Image", "illustration", "Table", "graphic")
        )
    ]
    reports = {}
    for name, items in cases.items():
        result = run_reference(
            items, python=a.python, source=a.source, lock=a.lock, java_home=a.java_home
        )
        independent = region_metrics(
            [i["pred_regions"] for i in items],
            [i["gt_regions"] for i in items],
            [i["all_regions"] for i in items],
        )
        assert all(
            abs(result["region"][key] - value) <= 1e-12
            for key, value in independent.items()
        )
        assert len(result["text"]) == 8
        if name == "perfect_punctuation_case":
            assert result["text"]["ROUGE_L"] == 1 and result["text"]["SPICE"] == 1
            assert result["by_region_type"]["graphic"]["question_count"] == 0
            assert result["by_region_type"]["graphic"]["text"] is None
        if name == "all_empty_predictions":
            assert (
                result["text"]["ROUGE_L"] == 0 and result["region"]["all_recall"] == 0
            )
            assert result["sample_count"] == 2
        if name == "all_six_region_types":
            assert all(
                v["question_count"] == 1 and v["text"] is not None
                for v in result["by_region_type"].values()
            )
        (a.out / (name + ".json")).write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n"
        )
        reports[name] = "pass"
        print(name, "passed", flush=True)
    (a.out / "summary.json").write_text(
        json.dumps(
            {
                "kind": "synthetic_full_reference_validation",
                "official_benchmark": False,
                "cases": reports,
                "region_tolerance": 1e-12,
                "text_path": "SHA-locked original compute_qa_score/remove_punc + full NLGEval; expected perfect/empty/punctuation/class behavior checked",
                "reference_lock_sha256": sha(a.lock),
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
