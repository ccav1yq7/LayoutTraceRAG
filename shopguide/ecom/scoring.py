"""Official reward invocation and conservative complete-denominator summaries."""

import copy
import json
import math
import random
from collections import defaultdict

from .upstream import scorer_class


class QuietConsole:
    def log(self, *args, **kwargs):
        pass

    def log_table(self, *args, **kwargs):
        pass


def score(root, task, baseline, data, adapter, session, elapsed):
    from .server import load_server

    load_server(root)
    from tools import ActionTools

    cls = scorer_class(root, ActionTools)
    env = cls.__new__(cls)
    env.task = task
    env.data_dir = str(baseline)
    env.cache_dir = str(data)
    env.session = copy.deepcopy(session)
    env.elapsed_time = elapsed
    env.max_time_limit = 30  # Pinned code's per-response time metric, not watchdog.
    env.tool_calls = []
    env.console_verbose = QuietConsole()
    # Native scorer itself reads additional_kwargs; do not synthesize required actions.
    env._save_tool_calls(adapter.scorer_messages())
    if len(env.tool_calls) != len(adapter.calls):
        raise ValueError("NATIVE_TRACE_COVERAGE_INVALID")
    if len(adapter.records) != len(adapter.calls):
        raise ValueError("UNRESOLVED_TOOL_OUTCOME")
    reward, action, search, output, timing = env.calculate_reward(session, elapsed, 1)
    return {
        "reward": reward,
        "action": action,
        "search": search,
        "output": output,
        "time": timing,
    }


def pass_power(c, n, k):
    if (
        not all(isinstance(v, int) and not isinstance(v, bool) for v in (c, n, k))
        or not 0 <= c <= n
        or not 1 <= k <= n
    ):
        raise ValueError("INVALID_PASS_POWER_COUNTS")
    return math.comb(c, k) / math.comb(n, k) if c >= k else 0.0


def summarize(manifest, rows):
    if (
        not manifest["methods"]
        or not manifest["task_ids"]
        or len(set(manifest["methods"])) != len(manifest["methods"])
        or len(set(manifest["task_ids"])) != len(manifest["task_ids"])
        or not 1 <= manifest["num_trials"] <= 8
    ):
        raise ValueError("INVALID_SCHEDULE")
    expected = {
        (method, task, trial)
        for method in manifest["methods"]
        for task in manifest["task_ids"]
        for trial in range(manifest["num_trials"])
    }
    keyed = {}
    for row in rows:
        key = (row["method"], row["task_id"], row["trial"])
        if key not in expected or key in keyed:
            raise ValueError("DUPLICATE_OR_UNSCHEDULED_TRIAL")
        if row.get("reward") not in (0, 1):
            raise ValueError("INVALID_REWARD")
        keyed[key] = row
    report: dict = {
        "scheduled": len(expected),
        "observed": len(keyed),
        "missing": len(expected - keyed.keys()),
        "complete": len(keyed) == len(expected),
        "official_benchmark": False,
        "methods": {},
    }
    for method in manifest["methods"]:
        values = defaultdict(list)
        failures: dict[str, int] = defaultdict(int)
        dimensions = defaultdict(list)
        for m, task, trial in sorted(expected):
            if m != method:
                continue
            row = keyed.get((m, task, trial))
            success = bool(
                row and row.get("status") == "completed" and row["reward"] == 1
            )
            values[task].append(int(success))
            failures[(row or {}).get("status", "missing")] += 1
            for name in ("action", "search", "output"):
                dimensions[name].append((row or {}).get(name, 0))
        n = manifest["num_trials"]
        ks = [k for k in (1, 3, 5, 8) if k <= n]
        estimates = {}
        rng = random.Random(manifest.get("seed", 0))
        for k in ks:
            per = [pass_power(sum(v), n, k) for v in values.values()]
            samples = sorted(
                sum(rng.choices(per, k=len(per))) / len(per) for _ in range(1000)
            )
            estimates[str(k)] = {
                "estimate": sum(per) / len(per),
                "task_bootstrap_95": [samples[24], samples[974]],
            }
        report["methods"][method] = {
            "pass_power_conservative": estimates,
            "counts": dict(failures),
            "dimensions": {key: sum(v) / len(v) for key, v in dimensions.items()},
            "task_successes": {str(t): sum(v) for t, v in values.items()},
        }
    if len(manifest["methods"]) == 2:
        first, second = manifest["methods"]
        diffs = [
            (
                report["methods"][second]["task_successes"][str(task)]
                - report["methods"][first]["task_successes"][str(task)]
            )
            / manifest["num_trials"]
            for task in manifest["task_ids"]
        ]
        rng = random.Random(manifest.get("seed", 0))
        samples = sorted(
            sum(rng.choices(diffs, k=len(diffs))) / len(diffs) for _ in range(1000)
        )
        report["paired_pass1_difference"] = {
            "contrast": second + " minus " + first,
            "estimate": sum(diffs) / len(diffs),
            "task_bootstrap_95": [samples[24], samples[974]],
        }
    return report


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
