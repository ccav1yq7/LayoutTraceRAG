"""Standalone evaluation worker; run with the isolated reference Python and -I.

Only SHA-locked upstream scoring definitions are executed, never training loaders.
No inference gateway or runtime corpus is imported here.
"""

import argparse
import ast
import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def definitions(path, names, namespace):
    tree = ast.parse(path.read_text())
    selected: list[ast.stmt] = []
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef)
            and node.name in names
            or isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
        ):
            selected.append(node)
    exec(  # noqa: S102 -- SHA-verified upstream definitions only
        compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"),
        namespace,
    )


def execute(source, lock, items):
    import nlgeval
    from sklearn.metrics import f1_score, precision_score, recall_score
    from transformers import T5TokenizerFast

    java_home = Path(os.environ["JAVA_HOME"])
    for relative, digest in lock["java"]["files"].items():
        if sha(java_home / relative) != digest:
            raise ValueError("REFERENCE_JAVA_CHANGED")
    package = Path(nlgeval.__file__).parent
    for relative, digest in lock["source_files"].items():
        if sha(source / relative) != digest:
            raise ValueError("REFERENCE_SOURCE_CHANGED")
    for relative, digest in lock["nlgeval_files"].items():
        if sha(package / relative) != digest:
            raise ValueError("NLGEVAL_RESOURCE_CHANGED")
    for name, version in lock["packages"].items():
        if importlib.metadata.version(name) != version:
            raise ValueError("REFERENCE_PACKAGE_CHANGED")
    from huggingface_hub import snapshot_download

    tokenizer_info = lock["tokenizer"]
    tokenizer_path = Path(
        snapshot_download(
            tokenizer_info["model_id"],
            revision=tokenizer_info["revision"],
            local_files_only=True,
        )
    )
    for relative, digest in tokenizer_info["files"].items():
        if sha(tokenizer_path / relative) != digest:
            raise ValueError("REFERENCE_TOKENIZER_CHANGED")
    tokenizer = T5TokenizerFast.from_pretrained(str(tokenizer_path), legacy=True)
    constants: dict = {}
    definitions(source / "dataset/const.py", {"VRM_SEMANTIC_TOKENS"}, constants)
    tokenizer.add_special_tokens(
        {"additional_special_tokens": constants["VRM_SEMANTIC_TOKENS"]}
    )
    # MPMQA's loader encodes '<pad>'+answer and drops EOS for answer_ids.
    # Its evaluator decodes answer_ids with skip_special_tokens=True.
    items = [
        dict(
            i,
            gt=tokenizer.decode(
                tokenizer("<pad>" + i["gt"]).input_ids[:-1], skip_special_tokens=True
            ),
        )
        for i in items
    ]
    scorer = nlgeval.NLGEval(no_skipthoughts=True, no_glove=True)
    namespace = {
        "nlgeval": scorer,
        "precision_score": precision_score,
        "recall_score": recall_score,
        "f1_score": f1_score,
    }
    definitions(
        source / "evaluate.py",
        {"PUNCTUATIONS", "remove_punc", "compute_visual_answer_metics"},
        namespace,
    )
    definitions(
        source / "scripts/compute_metrics.py",
        {
            "VRM_SEMANTIC_CLS2ID",
            "get_filter_list",
            "compute_qa_score",
            "compute_visual_answer_metrics",
        },
        namespace,
    )
    if not items:
        raise ValueError("EMPTY_REFERENCE_INPUT")
    with contextlib.redirect_stdout(io.StringIO()):
        text = namespace["compute_qa_score"](items)
        region = namespace["compute_visual_answer_metics"](
            [i["pred_regions"] for i in items],
            [i["gt_regions"] for i in items],
            [i["all_regions"] for i in items],
        )
        grouped = {}
        for kind in (
            "Text",
            "Title",
            "Product Image",
            "illustration",
            "Table",
            "graphic",
        ):
            subset = [i for i in items if kind in i["gt_region_cls"]]
            # Upstream crashes/NaNs on an empty class. Report absent, never drop it.
            grouped[kind] = {
                "question_count": len(subset),
                "text": namespace["compute_qa_score"](subset) if subset else None,
                "region": namespace["compute_visual_answer_metrics"](
                    [i["pred_regions"] for i in items],
                    [i["gt_regions"] for i in items],
                    [i["all_regions"] for i in items],
                    [i["all_region_cls"] for i in items],
                    cls_split=kind,
                )
                if any(kind in i["all_region_cls"] for i in items)
                else None,
            }
    expected = {
        "Bleu_1",
        "Bleu_2",
        "Bleu_3",
        "Bleu_4",
        "METEOR",
        "ROUGE_L",
        "CIDEr",
        "SPICE",
    }
    if set(text) != expected:
        raise ValueError("INCOMPLETE_REFERENCE_METRICS")
    return {
        "text": text,
        "region": region,
        "by_region_type": grouped,
        "sample_count": len(items),
    }


def main():
    parser = argparse.ArgumentParser()
    for name in ("source", "lock", "input", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = execute(
        args.source,
        json.loads(args.lock.read_text()),
        json.loads(args.input.read_text()),
    )
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
