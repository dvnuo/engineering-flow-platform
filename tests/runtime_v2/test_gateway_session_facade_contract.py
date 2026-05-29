from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import CompactionPart, MessagePart, MessageRole, ToolResult
from efp_runtime.permissions import PermissionDecision, PermissionMetadata, PermissionRequest
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.file_store import FileSessionStore
from efp_runtime.session.gateway_facade import RuntimeV2SessionManager
from efp_runtime.session.models import MessagePartType
from efp_runtime.tools.definition import ToolContext, ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


@pytest.mark.asyncio
async def test_gateway_facade_sees_runtime_loop_messages_tool_results_and_compaction(tmp_path: Path):
    (tmp_path / "workspace").mkdir()
    store = FileSessionStore(tmp_path / "store")
    manager = RuntimeV2SessionManager(store=store)

    async def echo(args: dict[str, Any], context: ToolContext) -> str:
        return f"echo:{args['text']}"

    runtime = AgentRuntime(
        provider=ScriptedLLMProvider(
            [
                {"tool_calls": [_tool_call("call-echo", "echo", {"text": "from-tool"})]},
                {"content": "final answer"},
            ]
        ),
        config=RuntimeConfig(workspace_root=tmp_path / "workspace", max_iterations=3),
        store=store,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="echo",
                        description="Echo input",
                        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                        execute=echo,
                    )
                ]
            )
        ),
    )

    result = await runtime.run("Use the echo tool.", session_id="contract-session")
    assert result.status == LoopStatus.COMPLETED

    source_ids = [message.message_id for message in store.read_history("contract-session")]
    store.append_message(
        "contract-session",
        role=MessageRole.SYSTEM,
        parts=[
            MessagePart.compaction_part(
                CompactionPart(
                    summary="Compacted prior context.",
                    source_message_ids=source_ids,
                    auto=True,
                    tail_start_message_id=source_ids[-1],
                )
            )
        ],
        status="complete",
    )

    store_history = store.read_history("contract-session")
    facade_history = await manager.get_history("contract-session")

    assert [item["id"] for item in facade_history] == [message.message_id for message in store_history]
    assert [message.role for message in store_history[:4]] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert any(part.type is MessagePartType.TOOL_RESULT for message in store_history for part in message.parts)
    assert any(part.type is MessagePartType.COMPACTION for message in store_history for part in message.parts)
    assert any(item.get("type") == "compaction_summary" for item in facade_history)
    assert facade_history[0]["content"] == "Use the echo tool."
    assert facade_history[2]["content"] == "echo:from-tool"
    assert facade_history[3]["content"] == "final answer"


@pytest.mark.asyncio
async def test_pending_permission_tool_call_survives_facade_metadata_and_resume(tmp_path: Path):
    (tmp_path / "workspace").mkdir()
    store = FileSessionStore(tmp_path / "store")
    manager = RuntimeV2SessionManager(store=store)
    evaluator = _AskOnceEvaluator()
    executed: list[ToolContext] = []

    async def gated(args: dict[str, Any], context: ToolContext) -> str:
        executed.append(context)
        return "approved"

    runtime = AgentRuntime(
        provider=ScriptedLLMProvider(
            [
                {"tool_calls": [_tool_call("call-gated", "gated", {})]},
                {"content": "resumed after permission"},
            ]
        ),
        config=RuntimeConfig(workspace_root=tmp_path / "workspace", max_iterations=3),
        store=store,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="gated",
                        description="Requires permission once",
                        input_schema={"type": "object", "properties": {}},
                        execute=gated,
                    )
                ]
            ),
            permission_evaluator=evaluator,
        ),
    )

    first = await runtime.run("Use gated.", session_id="permission-session")
    await manager.record_runtime_result("permission-session", first, request_id="req-permission")

    assert first.status == LoopStatus.WAITING_FOR_PERMISSION
    metadata = store.get_session("permission-session").metadata
    assert metadata["pending_permission_request"]["tool_id"] == "gated"
    assert metadata["pending_tool_calls"][0]["call_id"] == "call-gated"
    assert [message.role for message in store.read_history("permission-session")] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]

    resumed = await runtime.resume("permission-session", metadata={"run_id": "resume-permission"})
    await manager.record_runtime_result("permission-session", resumed, request_id="req-permission-resume")

    assert resumed.status == LoopStatus.COMPLETED
    assert executed and executed[0].tool_call_id == "call-gated"
    resumed_metadata = store.get_session("permission-session").metadata
    assert "pending_permission_request" not in resumed_metadata
    assert "pending_tool_calls" not in resumed_metadata
    assert [message.role for message in store.read_history("permission-session")] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]


@pytest.mark.asyncio
async def test_pending_question_state_is_recorded_on_runtime_v2_session(tmp_path: Path):
    (tmp_path / "workspace").mkdir()
    store = FileSessionStore(tmp_path / "store")
    manager = RuntimeV2SessionManager(store=store)

    async def ask_user(args: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(
            call_id=context.tool_call_id or "call-question",
            tool_name="ask_user",
            status="question_requested",
            success=False,
            content="Need clarification.",
            metadata={"question_request": {"id": "question-1", "prompt": "Continue?"}},
        )

    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"tool_calls": [_tool_call("call-question", "ask_user", {})]}]),
        config=RuntimeConfig(workspace_root=tmp_path / "workspace", max_iterations=2),
        store=store,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="ask_user",
                        description="Ask a question",
                        input_schema={"type": "object", "properties": {}},
                        execute=ask_user,
                    )
                ]
            )
        ),
    )

    result = await runtime.run("Ask before continuing.", session_id="question-session")
    await manager.record_runtime_result("question-session", result, request_id="req-question")

    assert result.status == LoopStatus.WAITING_FOR_QUESTION
    metadata = store.get_session("question-session").metadata
    assert metadata["pending_question_request"] == {"id": "question-1", "prompt": "Continue?"}
    assert metadata["pending_tool_calls"][0]["call_id"] == "call-question"
    facade = await manager.get_session("question-session")
    assert facade["metadata"]["pending_question_request"]["id"] == "question-1"


def _tool_call(call_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": tool_name, "arguments": json.dumps(arguments)},
    }


class _AskOnceEvaluator:
    def __init__(self) -> None:
        self.count = 0

    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: ToolContext | None = None,
    ) -> PermissionDecision:
        self.count += 1
        if self.count == 1:
            return PermissionDecision.ask(
                PermissionRequest(
                    session_id=context.session_id if context is not None else None,
                    tool_id=tool_id,
                    args=args,
                    reason="approval required",
                )
            )
        return PermissionDecision.allow()
