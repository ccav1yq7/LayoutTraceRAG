"""One environment/data/MCP lifetime per trial; actors see only permitted messages."""

import asyncio
import json
import shutil
import time
from pathlib import Path

from .native import native_server
from .scoring import score, write_json
from .upstream import data_hashes, definitions, scorer_class, task_models, tasks, verify


def fixture_task(root):
    # Independent synthetic task, never copied from an official expected answer.
    models = task_models(root)
    return models["Task"](
        annotator="shopguide-synthetic",
        user_id="u",
        shop_id="s",
        platform="p",
        instruction="请将订单 o 加急。",
        metadata=models["Validation"](
            actions=[
                models["Action"](
                    name="manage_urgent",
                    arguments={
                        "platform": "p",
                        "shop_id": "s",
                        "user_id": "u",
                        "order_id": "o",
                    },
                )
            ],
            searches=[
                models["Search"](
                    name="get_user_orders_info_tool",
                    arguments={"platform": "p", "shop_id": "s", "user_id": "u"},
                )
            ],
            outputs=["加急"],
        ),
    )


def seed_fixture(directory):
    directory.mkdir()
    write_json(
        directory / "orders.json",
        {"p": {"s": {"u": {"o": {"订单ID": "o", "是否加急": False}}}}},
    )


def user_simulator(root, gateway):
    from langchain_core.messages import AIMessage

    class Bridge:
        async def ainvoke(self, request, **kwargs):
            response = await asyncio.to_thread(
                gateway.complete, request["messages"], []
            )
            return {"messages": [AIMessage(content=response.get("content") or "")]}

    class LLM:
        def __init__(self, **kwargs):
            self.messages = []
            self.verbose = False

        def _initiate_agent(self):
            return Bridge()

    cls = definitions(root, "user/user.py", ["UserBased"], {"LLM": LLM})["UserBased"]
    return cls("deepseek-v4-flash-vision-exp")


async def conversation(root, task, adapter, method, config, budget):
    from .gateway import ChatGateway

    customer = user_simulator(root, ChatGateway(config, budget, "user"))
    customer.load_system_prompt(
        (root / "envs/story/wiki.md").read_text().format(instruction=task.instruction)
    )
    policy = (
        (root / "wikis/agent_wiki.md")
        .read_text()
        .format(platform=task.platform, shop_id=task.shop_id, user_id=task.user_id)
    )
    actor = ChatGateway(config, budget, "agent")
    messages = [{"role": "system", "content": policy}]
    session = [{"role": "assistant", "content": "亲，需要什么帮助吗？"}]
    elapsed: list[float] = []
    previous = ""
    stop = scorer_class(root, None)._is_done
    for _turn in range(20):
        customer_text = await customer.call(session[-1]["content"])
        if not customer_text:
            raise RuntimeError("EMPTY_USER_RESPONSE")
        if customer_text == previous:
            customer_text = "###STOP###"
        previous = customer_text
        session.append({"role": "user", "content": customer_text})
        write_json(adapter.journal.parent / "session.json", session)
        if stop(None, customer_text):
            return session, elapsed, "completed"
        messages.append({"role": "user", "content": customer_text})
        started = time.monotonic()
        response: dict = {}
        for step in range(16):
            force_final = method == "fixed-one-batch" and step >= 1
            response = await asyncio.to_thread(
                actor.complete, messages, adapter.schemas, force_final=force_final
            )
            calls = response.get("tool_calls", [])
            if force_final and calls:
                raise RuntimeError("FORCED_FINAL_RETURNED_TOOL_CALLS")
            messages.append(response)
            if not calls:
                if not response.get("content"):
                    raise RuntimeError("EMPTY_AGENT_RESPONSE")
                break
            for call in calls:
                if len(adapter.calls) >= 64:
                    raise RuntimeError("TRIAL_TOOL_BUDGET_EXHAUSTED")
                result = await adapter.execute(call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        else:
            raise RuntimeError("TURN_TOOL_LOOP_LIMIT")
        elapsed.append(max(time.monotonic() - started, 0.000001))
        session.append({"role": "assistant", "content": response["content"]})
        write_json(adapter.journal.parent / "session.json", session)
    return session, elapsed, "turn_limit"


async def execute(request):
    root, out = Path(request["upstream"]), Path(request["out"])
    out.mkdir(parents=True, exist_ok=True)
    verify(root)
    original_hashes = data_hashes(root / "envs/story/data")
    baseline, data = out / "baseline", out / "data"
    if request["method"] == "synthetic-native":
        seed_fixture(baseline)
        task = fixture_task(root)
    else:
        shutil.copytree(root / "envs/story/data", baseline)
        task = tasks(root)[request["task_id"]]
    shutil.copytree(baseline, data)
    started = time.monotonic()
    result = {
        "task_id": request["task_id"],
        "trial": request["trial"],
        "method": request["method"],
        "status": "failed",
        "reward": 0,
        "action": 0,
        "search": 0,
        "output": 0,
        "official_benchmark": False,
    }
    adapter = None
    try:
        async with native_server(
            root,
            data,
            out / "calls.jsonl",
            private_config=request.get("private_config"),
            budget=request.get("budget"),
        ) as adapter:
            write_json(out / "tool-schemas.json", adapter.schemas)
            if request["method"] == "synthetic-native":
                for i, name in enumerate(
                    ("get_user_orders_info_tool", "manage_urgent_tool")
                ):
                    arguments = {"platform": "p", "shop_id": "s", "user_id": "u"}
                    if i:
                        arguments["order_id"] = "o"
                    raw = await adapter.execute(
                        {
                            "id": f"call_synthetic_{i}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    )
                    if raw.get("isError"):
                        raise RuntimeError("NATIVE_SMOKE_TOOL_ERROR")
                session = [
                    {"role": "user", "content": task.instruction},
                    {"role": "assistant", "content": "已将订单 o 加急。"},
                ]
                elapsed, status = (
                    [max(time.monotonic() - started, 0.000001)],
                    "completed",
                )
            else:
                session, elapsed, status = await conversation(
                    root,
                    task,
                    adapter,
                    request["method"],
                    Path(request["private_config"]),
                    Path(request["budget"]),
                )
            write_json(out / "session.json", session)
        # Server is closed before observing final state and invoking destructive scorer cleanup.
        shutil.copytree(data, out / "final-data")
        write_json(
            out / "data-hashes.json",
            {"before": data_hashes(baseline), "after": data_hashes(data)},
        )
        details = score(root, task, baseline, data, adapter, session, elapsed)
        result.update(
            details,
            status="completed",
            termination=status,
            tool_calls=len(adapter.calls),
            tool_errors=sum(bool(r["result"].get("isError")) for r in adapter.records),
        )
    except Exception as error:  # noqa: BLE001 - trial boundary records failure before cleanup
        # Stable category only; never persist provider error bodies or credentials.
        result["error"] = type(error).__name__
        code = str(error)
        if code and all(c.isupper() or c.isdigit() or c == "_" for c in code):
            result["code"] = code
        if data.exists() and not (out / "final-data").exists():
            shutil.copytree(data, out / "final-data")
    finally:
        shutil.rmtree(data, ignore_errors=True)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["source_data_unchanged"] = original_hashes == data_hashes(
            root / "envs/story/data"
        )
        if not result["source_data_unchanged"]:
            result.update(status="source_modified", reward=0)
        write_json(out / "result.json", result)
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    result = asyncio.run(execute(json.loads(args.request.read_text())))
    raise SystemExit(0 if result["status"] == "completed" else 2)


if __name__ == "__main__":
    main()
