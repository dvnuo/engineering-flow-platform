from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.context import prepare_history_for_request
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.models import Message, MessagePart, MessageRole, ToolCall, ToolResult
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.models import Session
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


def _tool_pair(call_id: str = "call-search") -> tuple[Message, Message]:
    call = ToolCall(
        call_id=call_id,
        tool_name="search",
        arguments={"query": "runtime"},
    )
    result = ToolResult(
        call_id=call_id,
        tool_name="search",
        content="result payload",
    )
    return (
        Message(
            role="assistant",
            message_id=f"msg-{call_id}-call",
            parts=[MessagePart.tool_call_part(call)],
        ),
        Message(
            role="tool",
            message_id=f"msg-{call_id}-result",
            parts=[MessagePart.tool_result_part(result)],
        ),
    )


@pytest.mark.asyncio
async def test_max_context_chars_compacts_provider_request_metadata():
    store = InMemorySessionStore()
    provider = ScriptedLLMProvider([{"content": "Budgeted answer."}])
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        max_context_chars=420,
        context_reserve_chars=20,
    )
    session = Session(
        session_id="session-budget",
        messages=[
            Message.from_text("user", "u" * 300, message_id="msg-old-user"),
            Message.from_text("assistant", "a" * 300, message_id="msg-old-assistant"),
        ],
    )

    result = await runner.run(session=session, user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    compaction = request.provider_request.metadata["compaction"]
    assert request.prepared_request.compaction_applied is True
    assert request.prepared_request.compaction_metadata == compaction
    assert set(compaction) == {
        "max_parts",
        "max_chars",
        "reserve_chars",
        "compacted_part_count",
        "compacted_message_count",
        "compacted_tool_pair_count",
        "compacted_chars",
        "kept_chars",
    }
    assert compaction["max_parts"] is None
    assert compaction["max_chars"] == 420
    assert compaction["reserve_chars"] == 20
    assert compaction["compacted_part_count"] == 2
    assert compaction["compacted_message_count"] == 2
    assert compaction["compacted_tool_pair_count"] == 0
    assert compaction["compacted_chars"] == 600
    assert compaction["kept_chars"] > len("latest request")
    assert [message.role for message in request.provider_request.messages] == ["system", "user"]
    assert request.provider_request.messages[0].context[0].type == "compaction_summary"
    assert request.provider_request.messages[1].text == "latest request"


def test_max_context_chars_does_not_compact_when_under_budget():
    messages = [
        Message.from_text("user", "short request", message_id="msg-user"),
        Message.from_text("assistant", "short answer", message_id="msg-assistant"),
    ]

    prepared = prepare_history_for_request(messages, max_chars=1000)

    assert prepared.compaction_applied is False
    assert prepared.compaction_metadata == {}
    assert "compaction" not in prepared.request.metadata
    assert [message.text for message in prepared.request.messages] == [
        "short request",
        "short answer",
    ]


def test_context_budget_keeps_tool_call_result_pair_together():
    call_message, result_message = _tool_pair()
    messages = [
        Message.from_text("user", "u" * 700, message_id="msg-old"),
        call_message,
        result_message,
        Message.from_text("user", "now", message_id="msg-now"),
    ]

    prepared = prepare_history_for_request(messages, max_chars=520)

    assert prepared.compaction_applied is True
    assert prepared.compaction_metadata["compacted_tool_pair_count"] == 0
    calls = [
        call.call_id
        for message in prepared.request.messages
        for call in message.tool_calls
    ]
    results = [
        result.call_id
        for message in prepared.request.messages
        for result in message.tool_results
    ]
    assert calls == ["call-search"]
    assert results == ["call-search"]


def test_context_budget_does_not_drop_pending_tool_call():
    pending_call = ToolCall(
        call_id="call-pending",
        tool_name="write_file",
        arguments={"path": "created.txt", "content": "pending"},
    )
    messages = [
        Message.from_text("user", "u" * 500, message_id="msg-old"),
        Message(
            role="assistant",
            message_id="msg-pending",
            parts=[MessagePart.tool_call_part(pending_call)],
        ),
        Message.from_text("user", "latest", message_id="msg-latest"),
    ]

    prepared = prepare_history_for_request(messages, max_chars=180)

    assert prepared.compaction_applied is True
    pending_calls = [
        call.call_id
        for message in prepared.request.messages
        for call in message.tool_calls
    ]
    assert pending_calls == ["call-pending"]
    assert all(not message.tool_results for message in prepared.request.messages)


def test_context_budget_preserves_system_context_before_summary():
    messages = [
        Message.from_text(
            "system",
            "Skill context stays visible.",
            message_id="msg-skill",
            metadata={"kind": "skill_context"},
        ),
        Message.from_text("user", "u" * 500, message_id="msg-old"),
        Message.from_text("user", "latest", message_id="msg-latest"),
    ]

    prepared = prepare_history_for_request(messages, max_chars=300)

    assert prepared.compaction_applied is True
    assert prepared.request.messages[0].role == "system"
    assert prepared.request.messages[0].text == "Skill context stays visible."
    assert prepared.request.messages[1].context[0].type == "compaction_summary"


@pytest.mark.asyncio
async def test_agent_runtime_passes_config_max_context_chars_to_provider_request():
    provider = ScriptedLLMProvider([{"content": "Runtime answer."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            max_context_chars=420,
            context_reserve_chars=20,
        ),
    )
    runtime.store.create_session(session_id="session-runtime-budget")
    runtime.store.append_message(
        "session-runtime-budget",
        role=MessageRole.USER,
        parts=[MessagePart.text_part("u" * 300)],
        message_id="msg-old-user",
        status="complete",
    )
    runtime.store.append_message(
        "session-runtime-budget",
        role=MessageRole.ASSISTANT,
        parts=[MessagePart.text_part("a" * 300)],
        message_id="msg-old-assistant",
        status="complete",
    )

    result = await runtime.run("latest request", session_id="session-runtime-budget")

    assert result.status == LoopStatus.COMPLETED
    compaction = provider.requests[0].provider_request.metadata["compaction"]
    assert compaction["max_chars"] == 420
    assert compaction["reserve_chars"] == 20
    assert compaction["compacted_part_count"] == 2


def test_context_budget_source_stays_inside_runtime_v2_import_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime").rglob("*.py"))
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
    ]
    for token in forbidden_tokens:
        assert token not in combined


def test_context_budget_imports_standalone_with_pythonpath_src():
    code = """
import json
import sys

from efp_runtime.compaction import BudgetCompactionStrategy, ContextBudget

print(json.dumps({
    "legacy_core_loaded": "src.agents.core" in sys.modules,
    "strategy": BudgetCompactionStrategy.__name__,
    "budget": ContextBudget(max_chars=10).max_chars,
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
    assert payload == {
        "legacy_core_loaded": False,
        "strategy": "BudgetCompactionStrategy",
        "budget": 10,
    }
