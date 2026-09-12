"""Real MCP calls and lossless OpenAI-style call traces for the native scorer."""

import copy
import json
import sys
from contextlib import asynccontextmanager


class EComToolAdapter:
    def __init__(self, session, journal):
        self.session = session
        self.journal = journal
        self.schemas = []
        self.calls = []
        self.records = []
        self.seen_ids = set()

    async def discover(self):
        listed = await self.session.list_tools()
        self.schemas = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema,
                },
            }
            for t in listed.tools
        ]
        return self.schemas

    def log(self, event):
        with self.journal.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()

    async def execute(self, call):
        if (
            call.get("type") != "function"
            or not isinstance(call.get("id"), str)
            or not call["id"]
        ):
            raise ValueError("INVALID_TOOL_CALL")
        if call["id"] in self.seen_ids:
            raise ValueError("DUPLICATE_TOOL_CALL_ID")
        function = call["function"]
        if function["name"] not in {s["function"]["name"] for s in self.schemas}:
            raise ValueError("UNKNOWN_NATIVE_TOOL")
        arguments = json.loads(function["arguments"])
        if not isinstance(arguments, dict):
            raise TypeError("INVALID_TOOL_ARGUMENTS")
        self.seen_ids.add(call["id"])
        original = copy.deepcopy(call)
        self.log({"event": "tool.started", "call": original})
        # Record attempts before dispatch; a transport failure must not be retried as a write.
        self.calls.append(original)
        try:
            result = await self.session.call_tool(function["name"], arguments)
        except BaseException:
            self.log({"event": "tool.outcome_unknown", "id": call["id"]})
            raise
        raw = result.model_dump(mode="json", exclude_none=False)
        record = {"event": "tool.finished", "id": call["id"], "result": raw}
        self.records.append(record)
        self.log(record)
        return raw

    def scorer_messages(self):
        from langchain_core.messages import AIMessage

        return [
            AIMessage(
                content="", additional_kwargs={"tool_calls": copy.deepcopy(self.calls)}
            )
        ]


@asynccontextmanager
async def native_server(upstream, cache, journal, *, private_config=None, budget=None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    args = [
        "-m",
        "shopguide.ecom.server",
        "--upstream",
        str(upstream),
        "--cache",
        str(cache),
    ]
    if private_config:
        args += ["--private-config", str(private_config), "--budget", str(budget)]
    params = StdioServerParameters(command=sys.executable, args=args)
    with (cache.parent / "mcp-stderr.log").open("w") as err:
        async with stdio_client(params, errlog=err) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                adapter = EComToolAdapter(session, journal)
                await adapter.discover()
                yield adapter
