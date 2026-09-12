import argparse
import json
import sys
from pathlib import Path

from .runner import freeze, run_campaign, supervise
from .scoring import summarize, write_json
from .upstream import verify


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shopguide ecom")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("freeze", "smoke", "run"):
        p = sub.add_parser(command)
        p.add_argument("--upstream", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        if command == "freeze":
            p.add_argument("--num-trials", type=int, default=3)
            p.add_argument("--task-ids", type=int, nargs="+")
            p.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
        else:
            p.add_argument("--python", type=Path, default=Path(sys.executable))
        if command == "run":
            p.add_argument("--manifest", type=Path, required=True)
            p.add_argument("--private-config", type=Path, required=True)
            p.add_argument("--max-model-calls", type=int, required=True)
    p = sub.add_parser("report")
    p.add_argument("--campaign", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "freeze":
        result = freeze(
            args.upstream.resolve(),
            args.out,
            num_trials=args.num_trials,
            split=args.split,
            task_ids=args.task_ids,
        )
    elif args.command == "smoke":
        verify(args.upstream)
        result = supervise(
            {
                "upstream": str(args.upstream.resolve()),
                "out": str(args.out.resolve()),
                "method": "synthetic-native",
                "task_id": -1,
                "trial": 0,
            },
            timeout=60,
            python=args.python.absolute(),
        )
    elif args.command == "run":
        result = run_campaign(
            args.upstream,
            args.manifest,
            args.out,
            args.private_config,
            args.max_model_calls,
            python=args.python.absolute(),
        )
    else:
        manifest = json.loads((args.campaign / "manifest.json").read_text())
        rows = [json.loads(p.read_text()) for p in args.campaign.glob("*/result.json")]
        result = summarize(manifest, rows)
        write_json(args.campaign / "report.json", result)
    print(json.dumps(result, ensure_ascii=False))
    if args.command == "smoke" and result["status"] != "completed":
        raise SystemExit(2)
    if args.command in ("run", "report") and (
        not result["complete"]
        or any(
            v["counts"].get("budget_not_run", 0)
            or v["counts"].get("worker_failed", 0)
            or v["counts"].get("timeout", 0)
            or v["counts"].get("failed", 0)
            for v in result["methods"].values()
        )
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
