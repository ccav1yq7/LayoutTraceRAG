"""Executed only by the isolated native reference interpreter, not collected by pytest."""

import asyncio
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

# Make the checked-out adapter importable when this file is executed by path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from langchain_core.messages import AIMessage

from shopguide.ecom.scoring import score, write_json
from shopguide.ecom.trial import execute, fixture_task
from shopguide.ecom.upstream import verify


async def main(root, out):
    verify(root)
    for i in range(2):
        target = out / f"trial{i}"
        result = await execute(
            {
                "upstream": str(root),
                "out": str(target),
                "method": "synthetic-native",
                "task_id": -1,
                "trial": i,
            }
        )
        assert result["status"] == "completed" and result["reward"] == 1, result
        before = json.loads((target / "baseline/orders.json").read_text())
        after = json.loads((target / "final-data/orders.json").read_text())
        assert not before["p"]["s"]["u"]["o"]["是否加急"]
        assert after["p"]["s"]["u"]["o"]["是否加急"]
        assert not (target / "data").exists()
    target = out / "trial0"
    task = fixture_task(root)
    schemas = json.loads((target / "tool-schemas.json").read_text())
    events = [
        json.loads(line) for line in (target / "calls.jsonl").read_text().splitlines()
    ]
    calls = [event["call"] for event in events if event["event"] == "tool.started"]
    report = {"trials": 2, "native_tools": len(schemas)}
    for kind in ("missing_trace", "missing_write"):
        data = out / kind
        shutil.copytree(
            target / ("final-data" if kind == "missing_trace" else "baseline"), data
        )
        actual = [] if kind == "missing_trace" else calls
        adapter = SimpleNamespace(
            calls=actual,
            records=[{} for _ in actual],
            scorer_messages=lambda actual=actual: [
                AIMessage(content="", additional_kwargs={"tool_calls": actual})
            ],
        )
        result = score(
            root,
            task,
            target / "baseline",
            data,
            adapter,
            [{"role": "assistant", "content": "已加急"}],
            [1.0],
        )
        assert result["reward"] == 0, result
        assert result["search" if kind == "missing_trace" else "action"] == 0
        report[kind + "_reward"] = result["reward"]
    write_json(out / "checks.json", report)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
