import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from shopguide.ecom.gateway import attach_images, charge
from shopguide.ecom.native import EComToolAdapter
from shopguide.ecom.runner import supervise
from shopguide.ecom.scoring import pass_power, summarize


@pytest.mark.parametrize(
    "c,n,k,expected",
    [
        (8, 8, 8, 1),
        (2, 8, 3, 0),
        (4, 8, 1, 0.5),
        (3, 3, 3, 1),
        (0, 3, 1, 0),
        (2, 3, 2, 1 / 3),
    ],
)
def test_pass_power_is_all_success_not_pass_at_k(c, n, k, expected):
    assert pass_power(c, n, k) == expected


@pytest.mark.parametrize(
    "args", [(1, 2, 3), (3, 2, 1), (0, 0, 0), (-1, 3, 1), (True, 3, 1)]
)
def test_pass_power_rejects_invalid_counts(args):
    with pytest.raises(ValueError):
        pass_power(*args)


def test_missing_and_failed_trials_stay_in_denominator():
    manifest = {"methods": ["a"], "task_ids": [0, 1], "num_trials": 3}
    rows = [
        {"method": "a", "task_id": 0, "trial": 0, "status": "completed", "reward": 1},
        {"method": "a", "task_id": 0, "trial": 1, "status": "timeout", "reward": 0},
    ]
    report = summarize(manifest, rows)
    assert report["scheduled"] == 6 and report["missing"] == 4
    assert not report["complete"]
    assert report["methods"]["a"]["pass_power_conservative"]["1"]["estimate"] == 1 / 6
    assert report["methods"]["a"]["pass_power_conservative"]["3"]["estimate"] == 0
    with pytest.raises(ValueError, match="DUPLICATE"):
        summarize(manifest, rows + rows)
    with pytest.raises(ValueError, match="UNSCHEDULED"):
        summarize(manifest, [{**rows[0], "task_id": 53}])


def test_native_calls_preserve_raw_blocks_and_scorer_format(tmp_path):
    class Session:
        def __init__(self):
            self.dispatched = []

        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="native",
                        description="native",
                        inputSchema={"type": "object"},
                    )
                ]
            )

        async def call_tool(self, name, arguments):
            self.dispatched.append((name, arguments))
            return SimpleNamespace(
                model_dump=lambda **kw: {
                    "content": [
                        {"type": "image", "data": "unchanged", "mimeType": "image/png"}
                    ],
                    "isError": False,
                }
            )

    async def scenario():
        session = Session()
        adapter = EComToolAdapter(session, tmp_path / "trace.jsonl")
        await adapter.discover()
        call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "native", "arguments": '{"value": "中文"}'},
        }
        result = await adapter.execute(call)
        assert result["content"][0]["data"] == "unchanged"
        assert session.dispatched == [("native", {"value": "中文"})]
        msg = adapter.scorer_messages()[0]
        assert msg.additional_kwargs["tool_calls"] == [call]
        assert isinstance(
            msg.additional_kwargs["tool_calls"][0]["function"]["arguments"], str
        )
        with pytest.raises(ValueError, match="DUPLICATE"):
            await adapter.execute(call)
        with pytest.raises(ValueError, match="UNKNOWN"):
            await adapter.execute(
                {
                    **call,
                    "id": "call_2",
                    "function": {"name": "python", "arguments": "{}"},
                }
            )
        assert len(session.dispatched) == 1
        events = [json.loads(line) for line in adapter.journal.read_text().splitlines()]
        assert [event["event"] for event in events] == ["tool.started", "tool.finished"]

    asyncio.run(scenario())


def test_unknown_write_outcome_cannot_be_replayed(tmp_path):
    class Session:
        async def call_tool(self, *args):
            raise OSError("disconnected after dispatch")

    async def scenario():
        adapter = EComToolAdapter(Session(), tmp_path / "trace.jsonl")
        adapter.schemas = [{"function": {"name": "write"}}]
        call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "write", "arguments": "{}"},
        }
        with pytest.raises(OSError):
            await adapter.execute(call)
        assert len(adapter.calls) == 1 and not adapter.records
        with pytest.raises(ValueError, match="DUPLICATE"):
            await adapter.execute(call)
        assert "outcome_unknown" in adapter.journal.read_text()

    asyncio.run(scenario())


def test_model_budget_shared_by_user_agent_and_vision(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text('{"limit":3,"calls":0}')
    for role in ("agent", "user", "vision"):
        charge(path, role)
        charge(path, role, 10)
    with pytest.raises(RuntimeError, match="EXHAUSTED"):
        charge(path, "agent")
    state = json.loads(path.read_text())
    assert state["calls"] == 3 and state["reported_tokens"] == 30
    assert state["roles"] == {"agent": 1, "user": 1, "vision": 1}


def test_vision_preserves_text_and_rejects_private_urls():
    messages = [{"role": "user", "content": "图片 https://example.com/p.png"}]
    converted = attach_images(messages)
    assert converted[0]["content"][0]["text"] == messages[0]["content"]
    assert converted[0]["content"][1]["image_url"]["url"] == "https://example.com/p.png"
    assert isinstance(messages[0]["content"], str)
    with pytest.raises(ValueError):
        attach_images([{"role": "user", "content": "https://127.0.0.1/p.png"}])


def test_watchdog_records_failure_and_cleans_data(tmp_path):
    result = supervise(
        {
            "upstream": str(tmp_path / "missing"),
            "out": str(tmp_path / "trial"),
            "method": "synthetic-native",
            "task_id": -1,
            "trial": 0,
        },
        timeout=0.001,
    )
    assert result["reward"] == 0 and result["status"] == "timeout"
    assert not (tmp_path / "trial/data").exists()
    assert (tmp_path / "trial/result.json").exists()


def test_pinned_native_reference_isolation_and_negative_scoring(tmp_path):
    root = Path(__file__).resolve().parents[2]
    python = Path(os.environ.get("SG_ECOM_PYTHON", str(root / ".venv-ecom/bin/python")))
    upstream = root / "external/ECom-Bench"
    if not python.exists() or not upstream.exists():
        pytest.skip(
            "requires isolated ECom reference environment; separate CI job runs this gate"
        )
    result = subprocess.run(
        [
            str(python),
            str(root / "tests/shopguide/ecom_reference_checks.py"),
            str(upstream),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "checks.json").read_text())
    assert report["trials"] == 2 and report["native_tools"] == 21
    assert report["missing_trace_reward"] == 0
    assert report["missing_write_reward"] == 0


def test_deepseek_transport_preserves_native_schema_and_charges_failures(
    tmp_path, monkeypatch
):
    import urllib.error

    from shopguide.ecom import gateway as module

    captured = []
    budget = tmp_path / "budget.json"
    budget.write_text('{"limit":2,"calls":0}')
    monkeypatch.setattr(
        module,
        "gateway_from_config",
        lambda p: SimpleNamespace(
            provider="deepseek",
            model_id="deepseek-v4-flash-vision-exp",
            _url="https://api.deepseek.com/responses",
            _key="fixture-secret",
        ),
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "c",
                                        "type": "function",
                                        "function": {
                                            "name": "native",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"total_tokens": 11},
                }
            ).encode()

    class Opener:
        def open(self, request, timeout):
            captured.append(json.loads(request.data))
            if len(captured) > 1:
                raise urllib.error.HTTPError(
                    request.full_url, 503, "sensitive upstream body", {}, None
                )
            return Response()

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *a: Opener())
    client = module.ChatGateway(tmp_path / "private", budget, "agent")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "native",
                "parameters": {
                    "type": "object",
                    "properties": {"action": {"enum": ["查询", "取消"]}},
                },
            },
        }
    ]
    reply = client.complete([{"role": "user", "content": "question"}], tools)
    assert captured[0]["tools"] == tools
    assert reply["tool_calls"][0]["function"]["name"] == "native"
    assert captured[0]["thinking"] == {"type": "disabled"}
    with pytest.raises(RuntimeError, match="^MODEL_HTTP_503$"):
        client.complete([], tools)
    assert json.loads(budget.read_text())["calls"] == 2
    assert json.loads(budget.read_text())["reported_tokens"] == 11
    assert "fixture-secret" not in budget.read_text()


def test_task_instruction_and_gold_never_enter_service_actor(tmp_path, monkeypatch):
    from shopguide.ecom import gateway, trial

    (tmp_path / "envs/story").mkdir(parents=True)
    (tmp_path / "wikis").mkdir()
    (tmp_path / "envs/story/wiki.md").write_text("customer goal {instruction}")
    (tmp_path / "wikis/agent_wiki.md").write_text(
        "serve {platform} {shop_id} {user_id}"
    )
    requests = []

    class Gateway:
        def __init__(self, config, budget, role):
            self.role = role

        def complete(self, messages, tools, **kwargs):
            requests.append((self.role, json.dumps(messages)))
            return {"role": "assistant", "content": "已办理。"}

    class Customer:
        turns = 0

        def load_system_prompt(self, prompt):
            requests.append(("user", prompt))

        async def call(self, message):
            self.turns += 1
            return "办理加急" if self.turns == 1 else "###STOP###"

    monkeypatch.setattr(gateway, "ChatGateway", Gateway)
    monkeypatch.setattr(trial, "user_simulator", lambda root, gw: Customer())
    monkeypatch.setattr(
        trial,
        "scorer_class",
        lambda *a: SimpleNamespace(_is_done=lambda _, s: s == "###STOP###"),
    )
    task = SimpleNamespace(
        instruction="private_instruction",
        metadata={"outputs": ["hidden_gold_canary"]},
        platform="p",
        shop_id="s",
        user_id="u",
    )
    adapter = SimpleNamespace(journal=tmp_path / "calls.jsonl", schemas=[], calls=[])
    session, elapsed, status = asyncio.run(
        trial.conversation(tmp_path, task, adapter, "native-react", tmp_path, tmp_path)
    )
    assert status == "completed" and len(elapsed) == 1
    assert session[-1]["content"] == "###STOP###"
    assert all("hidden_gold_canary" not in body for _, body in requests)
    assert all(
        "private_instruction" not in body for role, body in requests if role == "agent"
    )
    assert any(
        "private_instruction" in body for role, body in requests if role == "user"
    )


def test_campaign_freeze_is_immutable_and_subset_cannot_cross_split(
    tmp_path, monkeypatch
):
    from shopguide.ecom import runner

    monkeypatch.setattr(
        runner, "verify", lambda root: {"revision": "fixture", "files": {}}
    )
    monkeypatch.setattr(
        runner,
        "tasks",
        lambda root: [SimpleNamespace(instruction="fixture") for _ in range(5)],
    )
    path = tmp_path / "frozen.json"
    summary = runner.freeze(tmp_path, path, num_trials=1, split="dev", task_ids=[1])
    assert summary["scheduled_trials"] == 2
    assert (
        path.with_suffix(".json.sha256").read_text().strip()
        == summary["manifest_sha256"]
    )
    manifest = json.loads(path.read_text())
    assert manifest["holdout_ids"] == [3, 4] and manifest["task_ids"] == [1]
    with pytest.raises(ValueError, match="ALREADY_EXISTS"):
        runner.freeze(tmp_path, path)
    with pytest.raises(ValueError, match="OUTSIDE_REGISTERED"):
        runner.freeze(tmp_path, tmp_path / "bad.json", task_ids=[4])
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="MANIFEST_DRIFT"):
        runner.run_campaign(
            tmp_path, path, tmp_path / "results", tmp_path / "never-read", 20
        )
    assert not (tmp_path / "results").exists()


def test_paired_report_uses_task_clusters_and_rejects_empty_schedule():
    manifest = {"methods": ["fixed", "adaptive"], "task_ids": [0, 1], "num_trials": 1}
    rows = [
        {
            "method": method,
            "task_id": task,
            "trial": 0,
            "status": "completed",
            "reward": int(method == "adaptive"),
        }
        for method in manifest["methods"]
        for task in manifest["task_ids"]
    ]
    report = summarize(manifest, rows)
    assert report["paired_pass1_difference"]["estimate"] == 1
    assert report["paired_pass1_difference"]["task_bootstrap_95"] == [1, 1]
    assert report["complete"]
    with pytest.raises(ValueError, match="INVALID_SCHEDULE"):
        summarize({**manifest, "task_ids": []}, [])
