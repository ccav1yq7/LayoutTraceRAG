"""Evaluation-side QA-to-page metrics; retain upstream's page-count weighting."""


def retrieval_metrics(rows, expected, manual_page_counts):
    byid = {r["question_id"]: r for r in rows}
    if len(byid) != len(rows) or not byid.keys() <= expected.keys() or not expected:
        raise ValueError("unknown/duplicate/empty retrieval schedule")
    manuals: dict[str, dict] = {}
    failures = []
    for qid, (manual, page) in expected.items():
        stats = manuals.setdefault(
            manual, {"questions": 0, "hits": {k: 0 for k in (1, 3, 5, 10)}}
        )
        stats["questions"] += 1
        row = byid.get(qid)
        if row is None:
            failures.append(qid)
            continue
        pages = row["retrieved_page_ids"]
        if row["manual_id"] != manual or len(set(pages)) != len(pages):
            raise ValueError("invalid retrieval identity/pages")
        for k in stats["hits"]:
            stats["hits"][k] += int(page in pages[:k])
    total_pages = sum(manual_page_counts[m] for m in manuals)
    if any(manual_page_counts[m] < 1 for m in manuals):
        raise ValueError("empty candidate manual")
    # MPMQA utils.merge_recall weights QA->page recalls by page_nums,
    # not by qa_nums and not an unweighted mean over manuals.
    weighted = {
        f"qa2page_r{k}": sum(
            v["hits"][k] / v["questions"] * manual_page_counts[m]
            for m, v in manuals.items()
        )
        / total_pages
        for k in (1, 3, 5, 10)
    }
    weighted["qa2page_r_mean"] = sum(weighted[f"qa2page_r{k}"] for k in (1, 3, 5)) / 3
    return {
        "upstream_compatible_qa2page": weighted,
        "diagnostic_question_micro": {
            f"recall@{k}": sum(v["hits"][k] for v in manuals.values()) / len(expected)
            for k in (1, 3, 5, 10)
        },
        "sample_count": len(expected),
        "prediction_count": len(rows),
        "manuals": len(manuals),
        "candidate_pages": total_pages,
        "missing_predictions": failures,
        "page2qa": None,
        "note": "QA-to-page component only; page2qa/r_mean not supplied. Original page-count weighting is retained. Validation subset, not full official benchmark.",
    }
