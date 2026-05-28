from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.tools.definition import ToolContext, ToolDef
from efp_runtime.tools.external import (
    ExternalToolContext,
    ExternalToolSpec,
    register_external_tools,
)
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.types import ToolCall


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_external_provider_registers_tool_and_runtime_normalizes_result():
    provider = _Provider(
        name="custom",
        specs=[
            ExternalToolSpec(
                name="echo",
                description="Echo external text",
                input_schema={
                    "type": "object",
                    "required": ["text"],
                    "properties": {"text": {"type": "string"}},
                    "additionalProperties": False,
                },
            )
        ],
    )
    registry = ToolRegistry()

    registered = register_external_tools(registry, [provider])
    runtime = ToolRuntime(registry)
    result = await runtime.execute(
        ToolCall(id="call-echo", tool_id="custom_echo", args={"text": "hello"}),
        context=ToolContext(session_id="session-1"),
    )

    assert registered == ["custom_echo"]
    assert result.status == "success"
    assert result.output == {"provider": "custom", "tool": "echo", "args": {"text": "hello"}}
    assert json.loads(result.content)["tool"] == "echo"
    assert provider.calls[0]["tool_name"] == "echo"

    tool = registry.require("custom_echo")
    assert tool.metadata["external_tool"] is True
    assert tool.metadata["external_provider"] == "custom"
    assert tool.metadata["external_tool_name"] == "echo"
    provider.specs[0].input_schema["properties"]["text"]["type"] = "integer"
    assert tool.input_schema["properties"]["text"]["type"] == "string"
    assert tool.permission.category == "external"
    assert tool.permission.resource == "custom/echo"
    assert tool.permission.risk == "medium"


@pytest.mark.asyncio
async def test_external_provider_async_execute_is_awaited():
    provider = _Provider(name="async_provider", specs=[ExternalToolSpec("lookup", "Lookup")])
    provider.async_execute = True
    registry = ToolRegistry()
    register_external_tools(registry, [provider])

    result = await ToolRuntime(registry).execute(
        ToolCall(id="call-async", tool_id="async_provider_lookup", args={})
    )

    assert result.status == "success"
    assert result.output["async"] is True
    assert provider.calls[0]["tool_name"] == "lookup"


@pytest.mark.asyncio
async def test_external_tool_schema_validation_prevents_provider_execution():
    provider = _Provider(
        name="strict",
        specs=[
            ExternalToolSpec(
                name="count",
                description="Count",
                input_schema={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "integer"}},
                    "additionalProperties": False,
                },
            )
        ],
    )
    registry = ToolRegistry()
    register_external_tools(registry, [provider])

    result = await ToolRuntime(registry).execute(
        ToolCall(id="call-invalid", tool_id="strict_count", args={"value": "1"})
    )

    assert result.status == "validation_error"
    assert "value" in result.error
    assert provider.calls == []


@pytest.mark.asyncio
async def test_external_tool_context_contains_runtime_fields_and_metadata_copy(
    tmp_path: Path,
):
    provider = _Provider(name="ctx", specs=[ExternalToolSpec("inspect", "Inspect")])
    registry = ToolRegistry()
    register_external_tools(registry, [provider])
    metadata = {
        "message_id": "message-1",
        "workspace_root": str(tmp_path),
        "nested": {"value": "original"},
    }

    result = await ToolRuntime(registry).execute(
        ToolCall(id="call-context", tool_id="ctx_inspect", args={}),
        context=ToolContext(session_id="session-ctx", metadata=metadata),
    )

    assert result.status == "success"
    external_context = provider.calls[0]["context"]
    assert isinstance(external_context, ExternalToolContext)
    assert external_context.session_id == "session-ctx"
    assert external_context.message_id == "message-1"
    assert external_context.tool_call_id == "call-context"
    assert external_context.workspace_root == str(tmp_path)
    assert external_context.provider_name == "ctx"
    assert external_context.tool_name == "inspect"
    assert external_context.runtime_metadata["nested"]["value"] == "mutated"
    assert metadata["nested"]["value"] == "original"
    assert "provider_added" not in metadata


@pytest.mark.asyncio
async def test_external_provider_exception_returns_normalized_tool_error():
    provider = _Provider(name="boom", specs=[ExternalToolSpec("fail", "Fail")])
    provider.raise_error = RuntimeError("provider failed")
    registry = ToolRegistry()
    register_external_tools(registry, [provider])

    result = await ToolRuntime(registry).execute(
        ToolCall(id="call-fail", tool_id="boom_fail", args={})
    )

    assert result.status == "error"
    assert result.success is False
    assert result.error == "provider failed"
    assert result.content == "provider failed"
    assert result.events[-1].type == "tool.error"


@pytest.mark.asyncio
async def test_external_registration_collision_errors_by_default_and_can_override():
    registry = ToolRegistry([_tool("custom_echo", output="internal")])
    provider = _Provider(name="custom", specs=[ExternalToolSpec("echo", "External")])

    with pytest.raises(ValueError, match="Tool already registered: custom_echo"):
        register_external_tools(registry, [provider])

    registered = register_external_tools(registry, [provider], allow_override=True)
    result = await ToolRuntime(registry).execute(
        ToolCall(id="call-override", tool_id="custom_echo", args={})
    )

    assert registered == ["custom_echo"]
    assert result.output["provider"] == "custom"
    assert registry.require("custom_echo").metadata["external_tool"] is True


@pytest.mark.asyncio
async def test_agent_runtime_exposes_external_tool_schema_and_executes_call(
    tmp_path: Path,
):
    provider = _Provider(
        name="acme",
        specs=[
            ExternalToolSpec(
                name="echo",
                description="Echo from acme",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "additionalProperties": False,
                },
            )
        ],
    )
    llm = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-acme", "acme_echo", {"text": "hi"})]},
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=llm,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=3),
        external_tool_providers=[provider],
    )

    result = await runtime.run("use acme", session_id="session-acme")

    assert result.status == LoopStatus.COMPLETED
    assert result.iterations == 2
    assert "acme_echo" in [tool.id for tool in llm.requests[0].tools]
    assert "acme_echo" in [schema.id for schema in llm.requests[0].provider_request.tools]
    assert provider.calls[0]["args"] == {"text": "hi"}
    assert provider.calls[0]["context"].session_id == "session-acme"
    assert provider.calls[0]["context"].workspace_root == str(tmp_path)


@pytest.mark.asyncio
async def test_disabled_tools_hides_external_tool_from_provider_request():
    provider = _Provider(name="acme", specs=[ExternalToolSpec("echo", "Echo")])
    llm = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=llm,
        config=RuntimeConfig(
            max_iterations=1,
            disabled_tools=["acme_echo"],
        ),
        external_tool_providers=[provider],
    )

    result = await runtime.run("no tools", session_id="session-disabled-external")

    assert result.status == LoopStatus.COMPLETED
    assert "acme_echo" not in [tool.id for tool in llm.requests[0].tools]
    assert "acme_echo" not in [schema.id for schema in llm.requests[0].provider_request.tools]
    assert provider.calls == []


def test_external_tools_import_boundary():
    code = """
import importlib
import json
import sys

importlib.import_module("efp_runtime.tools.external")
legacy_modules = [
    "src.sessions",
    "src.agents.core",
    "src.runtime",
    "src.skills",
]
print(json.dumps({
    "legacy_loaded": [name for name in legacy_modules if name in sys.modules],
}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"legacy_loaded": []}


class _Provider:
    def __init__(self, *, name: str, specs: list[ExternalToolSpec]) -> None:
        self.name = name
        self.specs = list(specs)
        self.calls: list[dict[str, Any]] = []
        self.async_execute = False
        self.raise_error: Exception | None = None

    def list_tools(self):
        return list(self.specs)

    def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ExternalToolContext,
    ):
        if self.async_execute:
            return self._execute_async(tool_name, args, context)
        return self._execute_sync(tool_name, args, context)

    async def _execute_async(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ExternalToolContext,
    ):
        output = self._execute_sync(tool_name, args, context)
        output["async"] = True
        return output

    def _execute_sync(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ExternalToolContext,
    ):
        if self.raise_error is not None:
            raise self.raise_error
        context.runtime_metadata["provider_added"] = True
        nested = context.runtime_metadata.get("nested")
        if isinstance(nested, dict):
            nested["value"] = "mutated"
        self.calls.append(
            {
                "tool_name": tool_name,
                "args": dict(args),
                "context": context,
            }
        )
        return {"provider": self.name, "tool": tool_name, "args": dict(args)}


def _tool(tool_id: str, *, output: str = "ok") -> ToolDef:
    async def execute(args, context):
        return output

    return ToolDef(
        id=tool_id,
        description=f"{tool_id} tool",
        input_schema={"type": "object", "properties": {}},
        execute=execute,
    )


def _tool_call(call_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(arguments, sort_keys=True),
        },
    }
