from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.event_bus import RuntimeEventBus
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.models import MessagePart, MessagePartType, MessageRole
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.types import ToolCall


ROOT = Path(__file__).resolve().parents[2]


def _write_file_call(path: str = "created.txt", content: str = "approved") -> dict[str, Any]:
    return {
        "id": "call_write",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": path, "content": content}),
        },
    }


@pytest.mark.asyncio
async def test_permission_request_waits_without_appending_tool_result(tmp_path: Path):
    bus = RuntimeEventBus()
    provider = ScriptedLLMProvider([{"tool_calls": [_write_file_call()]}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            include_legacy_tool_aliases=True,
        ),
        event_bus=bus,
    )

    result = await runtime.run("Write the file.", session_id="session-wait")

    assert result.status == LoopStatus.WAITING_FOR_PERMISSION
    assert result.pending_permission_request is not None
    assert result.pending_permission_request["tool_id"] == "write_file"
    assert (tmp_path / "created.txt").exists() is False

    history = runtime.store.read_history("session-wait")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert history[1].parts[0].type is MessagePartType.TOOL_CALL
    assert history[1].parts[0].tool_call.call_id == "call_write"
    assert not any(
        part.type is MessagePartType.TOOL_RESULT
        for message in history
        for part in message.parts
    )

    permission_events = [
        event for event in bus.history("session-wait") if event.type == "tool.permission_requested"
    ]
    assert len(permission_events) == 1
    assert permission_events[0].payload["tool_call_id"] == "call_write"
    assert permission_events[0].payload["tool_name"] == "write_file"
    assert permission_events[0].payload["permission_request"] == result.pending_permission_request
    assert bus.history("session-wait")[-1].type == "run_finish"
    assert bus.history("session-wait")[-1].payload["status"] == (
        LoopStatus.WAITING_FOR_PERMISSION
    )
    assert runtime.run_state.current("session-wait").active is False


@pytest.mark.asyncio
async def test_approve_then_resume_executes_pending_tool_call_without_empty_user(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_write_file_call(content="approved\n")]},
            {"content": "File written."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            include_legacy_tool_aliases=True,
        ),
    )

    first = await runtime.run("Write the file.", session_id="session-approve")
    request_id = first.pending_permission_request["request_id"]
    pending = runtime.pending_permissions()

    assert json.loads(json.dumps(pending))[0]["request_id"] == request_id

    runtime.approve_permission(request_id, always=False)
    resumed = await runtime.resume("session-approve")

    assert resumed.status == LoopStatus.COMPLETED
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "approved\n"
    assert len(provider.requests) == 2
    assert [message.role for message in provider.requests[1].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]

    history = runtime.store.read_history("session-approve")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert sum(1 for message in history if message.role is MessageRole.USER) == 1
    assert history[1].parts[0].tool_call.call_id == "call_write"
    assert history[2].parts[0].tool_result.call_id == "call_write"
    assert history[2].parts[0].tool_result.status == "success"
    assert history[3].parts[0].text == "File written."


@pytest.mark.asyncio
async def test_deny_then_resume_appends_denial_tool_result_for_provider(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_write_file_call(content="blocked")]},
            {"content": "I will not write the file."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            include_legacy_tool_aliases=True,
        ),
    )

    first = await runtime.run("Write the file.", session_id="session-deny")
    request_id = first.pending_permission_request["request_id"]

    runtime.deny_permission(request_id, always=False, reason="No writes now.")
    resumed = await runtime.resume("session-deny")

    assert resumed.status == LoopStatus.COMPLETED
    assert (tmp_path / "created.txt").exists() is False
    assert len(provider.requests) == 2
    assert [message.role for message in provider.requests[1].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]

    history = runtime.store.read_history("session-deny")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    denial = history[2].parts[0].tool_result
    assert denial.call_id == "call_write"
    assert denial.status == "permission_denied"
    assert denial.content == "No writes now."
    assert history[3].parts[0].text == "I will not write the file."


@pytest.mark.asyncio
async def test_loop_runner_append_user_message_false_resumes_existing_session():
    async def execute(args, context):
        return f"echo:{args['text']}:{context.session_id}"

    store = InMemorySessionStore()
    store.create_session(session_id="session-runner-resume")
    store.append_message(
        "session-runner-resume",
        role=MessageRole.USER,
        parts=[MessagePart.text_part("Use echo.")],
        status="complete",
    )
    store.append_message(
        "session-runner-resume",
        role=MessageRole.ASSISTANT,
        parts=[
            MessagePart.tool_call_part(
                ToolCall(id="call_echo", tool_id="echo", args={"text": "hello"})
            )
        ],
        status="complete",
    )
    provider = ScriptedLLMProvider([{"content": "Echo complete."}])
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="echo",
                        description="Echo text",
                        input_schema={
                            "type": "object",
                            "required": ["text"],
                            "properties": {"text": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        execute=execute,
                    )
                ]
            )
        ),
        max_iterations=2,
    )

    result = await runner.run(
        user_text="",
        session_id="session-runner-resume",
        append_user_message=False,
    )

    assert result.status == LoopStatus.COMPLETED
    history = store.read_history("session-runner-resume")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert history[2].parts[0].tool_result.call_id == "call_echo"
    assert history[2].parts[0].tool_result.content == (
        "echo:hello:session-runner-resume"
    )
    assert history[3].parts[0].text == "Echo complete."


def test_permission_resume_sources_stay_inside_runtime_v2_boundary():
    combined = "\n".join(
        [
            (ROOT / "src/efp_runtime/loop/runner.py").read_text(encoding="utf-8"),
            (ROOT / "src/efp_runtime/runtime/agent.py").read_text(encoding="utf-8"),
        ]
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.sessions",
        "import src.sessions",
        "from src.agents.core",
        "import src.agents.core",
        "from src.runtime",
        "import src.runtime",
        "from src.skills",
        "import src.skills",
    ]
    for token in forbidden_tokens:
        assert token not in combined


@pytest.mark.asyncio
async def test_default_core_tool_registry_is_used_for_permission_resume(tmp_path: Path):
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"tool_calls": [_write_file_call()]}]),
        tool_runtime=ToolRuntime(
            create_core_tool_registry(tmp_path, include_legacy_aliases=True)
        ),
    )

    result = await runtime.run("Write through injected core runtime.", session_id="session-core")

    assert result.status == LoopStatus.WAITING_FOR_PERMISSION
    assert result.pending_permission_request["tool_id"] == "write_file"
