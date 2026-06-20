from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.permissions import ASK, PermissionMetadata
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.models import MessagePart, MessagePartType, MessageRole
from efp_runtime.tools.builtin import create_structured_output_tool
from efp_runtime.tools.definition import ToolContext, ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.types import ToolCall


STRUCTURED_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["title", "count"],
    "properties": {
        "title": {"type": "string"},
        "count": {"type": "integer"},
    },
    "additionalProperties": False,
}


@pytest.mark.asyncio
async def test_structured_output_tool_validates_and_returns_terminal_metadata():
    tool = create_structured_output_tool(STRUCTURED_OUTPUT_SCHEMA)
    runtime = ToolRuntime(ToolRegistry([tool]))

    missing = await runtime.execute(
        ToolCall(
            id="call-missing",
            tool_id="StructuredOutput",
            args={"title": "done"},
        )
    )

    assert missing.status == "validation_error"
    assert "Missing required argument" in missing.content

    result = await runtime.execute(
        ToolCall(
            id="call-structured",
            tool_id="StructuredOutput",
            args={"title": "done", "count": 2},
        )
    )

    assert result.status == "success"
    assert result.content == "Structured output captured successfully."
    assert result.output == {"title": "done", "count": 2}
    assert result.metadata["terminal"] is True
    assert result.metadata["terminal_reason"] == "structured_output"
    assert result.metadata["structured_output"] == {"title": "done", "count": 2}
    assert result.metadata["valid"] is True


def test_structured_output_tool_ignores_schema_key_and_does_not_mutate_caller_schema():
    caller_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    original = dict(caller_schema)

    tool = create_structured_output_tool(caller_schema)

    assert caller_schema == original
    assert "$schema" not in tool.input_schema
    assert tool.input_schema["type"] == "object"
    assert tool.input_schema["required"] == ["name"]


def test_structured_output_tool_requires_object_schema():
    with pytest.raises(ValueError, match="object"):
        create_structured_output_tool({"type": "array", "items": {"type": "string"}})

    with pytest.raises(ValueError, match="properties"):
        create_structured_output_tool({})


@pytest.mark.asyncio
async def test_run_output_schema_exposes_tool_and_provider_only_reminder(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    _tool_call(
                        "call-structured",
                        "StructuredOutput",
                        {"title": "done", "count": 3},
                    )
                ]
            }
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            enabled_tools=["read"],
        ),
    )

    result = await runtime.run(
        "Return a structured result.",
        session_id="session-structured-request",
        output_schema=STRUCTURED_OUTPUT_SCHEMA,
    )

    assert result.status == LoopStatus.COMPLETED
    assert result.structured_output == {"title": "done", "count": 3}

    request = provider.requests[0]
    request_tool_ids = [tool.id for tool in request.tools]
    schema_ids = [schema.id for schema in request.provider_request.tools]
    assert "StructuredOutput" in request_tool_ids
    assert "StructuredOutput" in schema_ids
    structured_schema = next(
        schema for schema in request.provider_request.tools if schema.id == "StructuredOutput"
    )
    assert structured_schema.json_schema["required"] == ["title", "count"]
    assert request.metadata["structured_output"] is True
    assert request.metadata["structured_output_tool_id"] == "StructuredOutput"

    message_text = "\n".join(message.text for message in request.provider_request.messages)
    assert "The user has requested structured output" in message_text
    assert "StructuredOutput tool" in message_text
    assert "Do NOT respond with plain text" in message_text

    history = runtime.store.read_history("session-structured-request")
    assert all(message.role is not MessageRole.SYSTEM for message in history)


@pytest.mark.asyncio
async def test_structured_output_terminal_stops_without_second_provider_call(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    _tool_call(
                        "call-final",
                        "StructuredOutput",
                        {"title": "final", "count": 1},
                    )
                ]
            },
            {"content": "should not be used"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=4),
    )

    result = await runtime.run(
        "Return structured output.",
        session_id="session-structured-terminal",
        output_schema=STRUCTURED_OUTPUT_SCHEMA,
    )

    assert result.status == LoopStatus.COMPLETED
    assert result.iterations == 1
    assert len(provider.requests) == 1
    assert result.structured_output == {"title": "final", "count": 1}

    history = runtime.store.read_history("session-structured-terminal")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    tool_result = history[2].parts[0].tool_result
    assert tool_result is not None
    assert tool_result.tool_name == "StructuredOutput"
    assert tool_result.metadata["terminal"] is True
    assert tool_result.metadata["terminal_reason"] == "structured_output"
    assert tool_result.metadata["structured_output"] == {"title": "final", "count": 1}
    assert any(event.type == "tool_terminal" for event in result.runtime_events)


@pytest.mark.asyncio
async def test_plain_text_with_output_schema_returns_missing_structured_output_error(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "plain text"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=2),
    )

    result = await runtime.run(
        "Return structured output.",
        session_id="session-structured-missing",
        output_schema=STRUCTURED_OUTPUT_SCHEMA,
    )

    assert result.status == LoopStatus.ERROR
    assert result.structured_output is None
    missing_event = next(
        event
        for event in result.runtime_events
        if event.type == "structured_output.missing"
    )
    assert missing_event.payload["run_id"]
    assert missing_event.payload["tool_id"] == "StructuredOutput"
    assert missing_event.payload["iterations"] == 1
    assert missing_event.payload["prior_status"] == LoopStatus.COMPLETED

    history = runtime.store.read_history("session-structured-missing")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert history[1].parts[0].text == "plain text"


@pytest.mark.asyncio
async def test_invalid_structured_output_at_max_iterations_becomes_error(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    _tool_call(
                        "call-invalid",
                        "StructuredOutput",
                        {"title": "missing count"},
                    )
                ]
            }
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
    )

    result = await runtime.run(
        "Return structured output.",
        session_id="session-structured-invalid",
        output_schema=STRUCTURED_OUTPUT_SCHEMA,
    )

    assert result.status == LoopStatus.ERROR
    assert result.iterations == 1
    assert result.structured_output is None
    assert any(event.type == "loop.max_iterations" for event in result.runtime_events)
    missing_event = next(
        event
        for event in result.runtime_events
        if event.type == "structured_output.missing"
    )
    assert missing_event.payload["run_id"]
    assert missing_event.payload["tool_id"] == "StructuredOutput"
    assert missing_event.payload["iterations"] == 1
    assert missing_event.payload["prior_status"] == LoopStatus.MAX_ITERATIONS

    request = provider.requests[0]
    assert request.provider_request.tools == []
    assert "CRITICAL - MAXIMUM STEPS REACHED" in request.provider_request.messages[-1].text

    history = runtime.store.read_history("session-structured-invalid")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert history[1].parts[0].type is MessagePartType.TOOL_CALL


@pytest.mark.asyncio
async def test_waiting_for_permission_is_not_structured_output_error(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [{"tool_calls": [_tool_call("call-approval", "approval_required", {})]}]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=2),
        tool_registry=ToolRegistry([_permission_tool("approval_required")]),
    )

    result = await runtime.run(
        "Return structured output after approval.",
        session_id="session-structured-permission",
        output_schema=STRUCTURED_OUTPUT_SCHEMA,
    )

    assert result.status == LoopStatus.WAITING_FOR_PERMISSION
    assert result.pending_permission_request is not None
    assert result.structured_output is None
    assert not any(
        event.type == "structured_output.missing" for event in result.runtime_events
    )


@pytest.mark.asyncio
async def test_structured_output_not_visible_without_schema(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=2),
    )

    result = await runtime.run("Run normally.", session_id="session-no-structured")

    assert result.status == LoopStatus.COMPLETED
    assert result.structured_output is None
    assert "StructuredOutput" not in runtime.tool_runtime.registry.ids()
    assert "StructuredOutput" not in [
        tool.id for tool in provider.requests[0].provider_request.tools
    ]


@pytest.mark.asyncio
async def test_structured_output_can_be_explicitly_disabled_for_run(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "plain text"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=2),
    )

    result = await runtime.run(
        "Return structured output.",
        session_id="session-structured-disabled",
        output_schema=STRUCTURED_OUTPUT_SCHEMA,
        tools={"StructuredOutput": False},
    )

    assert result.status == LoopStatus.ERROR
    assert "StructuredOutput" not in [tool.id for tool in provider.requests[0].tools]
    assert "StructuredOutput" not in [
        schema.id for schema in provider.requests[0].provider_request.tools
    ]
    assert any(event.type == "structured_output.missing" for event in result.runtime_events)


@pytest.mark.asyncio
async def test_config_schema_works_for_run_and_resume(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    _tool_call(
                        "call-run",
                        "StructuredOutput",
                        {"title": "run", "count": 1},
                    )
                ]
            },
            {
                "tool_calls": [
                    _tool_call(
                        "call-resume",
                        "StructuredOutput",
                        {"title": "resume", "count": 2},
                    )
                ]
            },
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=2,
            structured_output_schema=STRUCTURED_OUTPUT_SCHEMA,
        ),
    )

    run_result = await runtime.run("Use config schema.", session_id="session-config-run")

    runtime.store.create_session(session_id="session-config-resume")
    runtime.store.append_message(
        "session-config-resume",
        role=MessageRole.USER,
        parts=[MessagePart.text_part("Resume with config schema.")],
        status="complete",
    )
    resume_result = await runtime.resume("session-config-resume")

    assert run_result.status == LoopStatus.COMPLETED
    assert run_result.structured_output == {"title": "run", "count": 1}
    assert resume_result.status == LoopStatus.COMPLETED
    assert resume_result.structured_output == {"title": "resume", "count": 2}
    assert all(
        "StructuredOutput" in [tool.id for tool in request.tools]
        for request in provider.requests
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


def _permission_tool(tool_id: str) -> ToolDef:
    async def execute(args: dict[str, Any], context: ToolContext):
        return "unused"

    return ToolDef(
        id=tool_id,
        description="Requires approval",
        input_schema={"type": "object", "properties": {}},
        permission=PermissionMetadata(
            action=ASK,
            reason="Approval required.",
        ),
        execute=execute,
    )
