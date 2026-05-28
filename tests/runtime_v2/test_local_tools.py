from __future__ import annotations

import json
from pathlib import Path

import pytest

from efp_runtime.config_loader import load_runtime_config
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime
from efp_runtime.session.models import MessagePartType
from efp_runtime.tools.local import local_tool_defs, register_local_tools
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.types import ToolCall


@pytest.mark.asyncio
async def test_default_tool_file_is_loaded_exposed_and_executed(tmp_path: Path):
    tool_file = tmp_path / ".opencode" / "tool" / "hello.py"
    _write_tool(
        tool_file,
        """
        def run(args, context):
            return f"hello {args['name']} in {context.session_id}"

        TOOL = {
            "description": "Say hello",
            "input_schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
                "additionalProperties": False,
            },
            "execute": run,
        }
        """,
    )

    loaded = load_runtime_config(tmp_path)
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-hello", "hello", {"name": "Ada"})]},
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=loaded.config,
        max_iterations=3,
    )

    result = await runtime.run("say hello", session_id="session-local-tool")

    assert loaded.config.local_tool_directories == [tool_file.parent.resolve()]
    assert result.status == LoopStatus.COMPLETED
    assert "hello" in [tool.id for tool in provider.requests[0].tools]
    assert "hello" in [
        schema.id for schema in provider.requests[0].provider_request.tools
    ]
    tool_result = _first_tool_result(runtime, "session-local-tool")
    assert tool_result.content == "hello Ada in session-local-tool"

    tool = runtime.tool_runtime.registry.require("hello")
    assert tool.metadata["local_tool"] is True
    assert tool.metadata["local_tool_file"] == str(tool_file.resolve())
    assert tool.metadata["local_tool_export"] == "TOOL"


@pytest.mark.asyncio
async def test_tools_mapping_registers_file_stem_and_export_name(tmp_path: Path):
    tool_file = tmp_path / ".opencode" / "tools" / "math.py"
    _write_tool(
        tool_file,
        """
        def add(args, context):
            return {"sum": args["a"] + args["b"]}

        TOOLS = {
            "add": {
                "description": "Add integers",
                "input_schema": {
                    "type": "object",
                    "required": ["a", "b"],
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                "execute": add,
            }
        }
        """,
    )
    registry = ToolRegistry()

    registered = register_local_tools(registry, [tool_file.parent])
    result = await ToolRuntime(registry).execute(
        ToolCall(id="call-add", tool_id="math_add", args={"a": 2, "b": 3})
    )

    assert registered == ["math_add"]
    assert result.status == "success"
    assert result.output == {"sum": 5}
    assert registry.require("math_add").metadata["local_tool_export"] == "TOOLS.add"


def test_unrelated_exports_are_ignored(tmp_path: Path):
    tool_file = tmp_path / "tools" / "clean.py"
    _write_tool(
        tool_file,
        """
        unrelated = {"description": "Ignore me"}

        def helper():
            return "not a tool"

        def run(args, context):
            return "ok"

        TOOL = {
            "description": "The only valid export",
            "execute": run,
        }
        """,
    )

    tools = local_tool_defs([tool_file.parent])

    assert [tool.id for tool in tools] == ["clean"]
    assert tools[0].description == "The only valid export"


@pytest.mark.asyncio
async def test_missing_input_schema_defaults_to_no_args_schema(tmp_path: Path):
    tool_file = tmp_path / "tools" / "noop.py"
    _write_tool(
        tool_file,
        """
        def run():
            return "ok"

        TOOL = {
            "description": "No args",
            "execute": run,
        }
        """,
    )
    registry = ToolRegistry()
    register_local_tools(registry, [tool_file.parent])

    success = await ToolRuntime(registry).execute(
        ToolCall(id="call-ok", tool_id="noop", args={})
    )
    failure = await ToolRuntime(registry).execute(
        ToolCall(id="call-bad", tool_id="noop", args={"extra": True})
    )

    assert registry.require("noop").input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert success.status == "success"
    assert success.content == "ok"
    assert failure.status == "validation_error"
    assert "Unexpected argument(s): extra" in failure.error


@pytest.mark.asyncio
async def test_sync_and_async_execute_functions_work(tmp_path: Path):
    tool_dir = tmp_path / "tools"
    _write_tool(
        tool_dir / "sync_echo.py",
        """
        def run(args):
            return args["text"].upper()

        TOOL = {
            "description": "Sync echo",
            "input_schema": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            },
            "execute": run,
        }
        """,
    )
    _write_tool(
        tool_dir / "async_echo.py",
        """
        async def run(args, context):
            return {"text": args["text"], "session": context.session_id}

        TOOL = {
            "description": "Async echo",
            "input_schema": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            },
            "execute": run,
        }
        """,
    )
    registry = ToolRegistry()
    register_local_tools(registry, [tool_dir])
    runtime = ToolRuntime(registry)

    sync_result = await runtime.execute(
        ToolCall(id="call-sync", tool_id="sync_echo", args={"text": "hello"})
    )
    async_result = await runtime.execute(
        ToolCall(id="call-async", tool_id="async_echo", args={"text": "hi"}),
        context=None,
    )

    assert sync_result.content == "HELLO"
    assert async_result.output == {"text": "hi", "session": None}


def test_permission_and_output_policy_mappings_are_supported(tmp_path: Path):
    tool_file = tmp_path / "tools" / "policy.py"
    _write_tool(
        tool_file,
        """
        def run(args, context):
            return "abcdef"

        TOOL = {
            "description": "Policy tool",
            "execute": run,
            "permission": {
                "action": "ask",
                "category": "local",
                "resource": "policy",
                "risk": "medium",
                "reason": "Needs review",
                "patterns": ["secret"],
            },
            "output_policy": {"max_chars": 4},
        }
        """,
    )

    tool = local_tool_defs([tool_file.parent])[0]

    assert tool.permission.action == "ask"
    assert tool.permission.category == "local"
    assert tool.permission.resource == "policy"
    assert tool.permission.risk == "medium"
    assert tool.permission.reason == "Needs review"
    assert tool.permission.data["patterns"] == ["secret"]
    assert tool.output_policy.max_chars == 4


def test_colliding_local_tool_ids_raise_by_default(tmp_path: Path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    _write_tool(
        first_dir / "one.py",
        """
        def run(args, context):
            return "one"

        TOOL = {"id": "same", "description": "One", "execute": run}
        """,
    )
    _write_tool(
        second_dir / "two.py",
        """
        def run(args, context):
            return "two"

        TOOL = {"name": "same", "description": "Two", "execute": run}
        """,
    )

    with pytest.raises(ValueError, match="Tool already registered: same"):
        register_local_tools(ToolRegistry(), [first_dir, second_dir])

    registry = ToolRegistry()
    assert register_local_tools(
        registry,
        [first_dir, second_dir],
        allow_override=True,
    ) == ["same", "same"]
    assert registry.require("same").description == "Two"


def test_javascript_and_typescript_files_are_not_loaded(tmp_path: Path):
    tool_dir = tmp_path / ".opencode" / "tool"
    _write_tool(
        tool_dir / "safe.py",
        """
        def run(args, context):
            return "safe"

        TOOL = {"description": "Safe", "execute": run}
        """,
    )
    _write_raw(tool_dir / "bad.js", "this is not valid Python syntax")
    _write_raw(tool_dir / "bad.ts", "this is not valid Python syntax")

    registry = ToolRegistry()
    registered = register_local_tools(registry, [tool_dir])

    assert registered == ["safe"]
    assert registry.ids() == ["safe"]


def _tool_call(call_id: str, tool_name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(arguments, sort_keys=True),
        },
    }


def _first_tool_result(runtime: AgentRuntime, session_id: str):
    for message in runtime.store.read_history(session_id):
        for part in message.parts:
            if (
                part.type is MessagePartType.TOOL_RESULT
                and part.tool_result is not None
            ):
                return part.tool_result
    raise AssertionError("No tool result found")


def _write_tool(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = content.strip("\n").splitlines()
    text = "\n".join(
        line[8:] if line.startswith("        ") else line for line in lines
    )
    path.write_text(text + "\n", encoding="utf-8")


def _write_raw(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
