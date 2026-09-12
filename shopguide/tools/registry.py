from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from pydantic import ValidationError

from ..schemas import Contract, Cost, SearchScope, ToolAction, ToolError, ToolResult


def arguments_hash(arguments: dict) -> str:
    """Canonical JSON: object ordering/whitespace irrelevant; array order retained."""
    return hashlib.sha256(
        json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ToolDefinition:
    parameters: type[Contract]
    handler: Callable[[Contract, SearchScope], dict]
    read_only: bool = True


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, name: str, definition: ToolDefinition):
        if name in self._tools:
            raise ValueError("duplicate tool name")
        self._tools[name] = definition

    def schemas(self) -> dict:
        return {
            name: d.parameters.model_json_schema() for name, d in self._tools.items()
        }

    def execute(
        self, action: ToolAction, scope: SearchScope | None, call_id: str
    ) -> ToolResult:
        started = monotonic()

        def error(code, message):
            return ToolResult(
                call_id=call_id,
                status="error",
                error=ToolError(code=code, message=message),
                cost=Cost(latency_ms=(monotonic() - started) * 1000),
            )

        if scope is None:
            return error("SCOPE_REQUIRED", "A trusted backend scope is required")
        definition = self._tools.get(action.tool_name)
        if definition is None:
            return error("UNKNOWN_TOOL", "Tool is not registered")
        if not definition.read_only:
            return error(
                "CONFIRMATION_REQUIRED",
                "Writes require a confirmed execution ledger; unavailable in M1",
            )
        try:
            parameters = definition.parameters.model_validate(action.arguments)
            arguments_hash(parameters.model_dump(mode="json"))
        except (ValidationError, ValueError, TypeError):
            return error("INVALID_ARGUMENTS", "Tool arguments failed validation")
        try:
            data = definition.handler(parameters, scope)
            # Refuse non-JSON results rather than allowing arbitrary objects to escape.
            json.dumps(data, allow_nan=False)
            return ToolResult(
                call_id=call_id,
                status="ok" if data else "empty",
                data=data,
                cost=Cost(latency_ms=(monotonic() - started) * 1000),
            )
        except PermissionError:
            return error("FORBIDDEN", "Access denied")
        except TimeoutError:
            return error("TIMEOUT", "Tool timed out")
        except Exception:  # noqa: BLE001 - tool boundary must redact handler failures
            return error("INTERNAL_ERROR", "Tool execution failed")
