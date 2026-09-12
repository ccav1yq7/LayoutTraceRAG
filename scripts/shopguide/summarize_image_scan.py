"""Conservative image vulnerability gate; never hides unfixed or duplicate matches."""

import argparse
import json
from collections import Counter
from pathlib import Path


def summarize(report, expected_image):
    if report.get("Metadata", {}).get("ImageID") != expected_image:
        raise ValueError("SCAN_IMAGE_MISMATCH")
    results = report.get("Results")
    if not isinstance(results, list) or not any(
        r.get("Class") == "os-pkgs" and r.get("Packages") for r in results
    ):
        raise ValueError("OS_PACKAGE_COVERAGE_MISSING")
    matches = [v for r in results for v in r.get("Vulnerabilities", [])]
    counts = Counter(v["Severity"] for v in matches)
    severe = [v for v in matches if v["Severity"] in ("HIGH", "CRITICAL")]
    return {
        "image_id": expected_image,
        "status": "fail" if severe else "pass_known_high_critical",
        "matches_by_severity": dict(counts),
        "unique_vulnerability_ids": len({v["VulnerabilityID"] for v in matches}),
        "high_critical_matches": len(severe),
        "high_critical_with_available_fix": sum(
            bool(v.get("FixedVersion")) for v in severe
        ),
        "unfixed_not_filtered": True,
        "coverage": "scanner-detected OS/language packages; not proof of complete embedded-library coverage",
        "targets": [
            {
                "target": r["Target"],
                "class": r.get("Class"),
                "packages": len(r.get("Packages", [])),
            }
            for r in results
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(json.loads(args.scan.read_text()), args.image_id)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    if report["status"] == "fail":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
