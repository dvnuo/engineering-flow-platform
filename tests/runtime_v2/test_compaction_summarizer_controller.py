from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.compaction import (
    CompactionRequest,
    CompactionSummary,
    ContextBudget,
    maybe_summarize_compaction,
)
from efp_runtime.context import prepare_history_for_request
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.models import Message, MessagePart, MessageRole, ToolCall, ToolResult
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.models import Session
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


def _runner(provider, summarizer=None, *, max_parts=None, max_chars=None):
    return RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        max_context_parts=max_parts,
        max_context_chars=max_chars,
        compaction_summarizer=summarizer,
    )


def _old_session() -> Session:
    return Session(
        session_id="session-compact-summary",
        messages=[
            Message.from_text(
                "user",
                "old request",
                session_id="session-compact-summary",
                message_id="msg-old-user",
            ),
            Message.from_text(
                "assistant",
                "old answer",
                session_id="session-compact-summary",
                message_id="msg-old-assistant",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_under_budget_does_not_call_summarizer():
    calls = []

    def fake(request: CompactionRequest) -> str:
        calls.append(request)
        return "unused"

    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(provider, fake, max_chars=1000)

    result = await runner.run(session_id="session-under-budget", user_text="short")

    assert result.status == LoopStatus.COMPLETED
    assert calls == []
    assert provider.requests[0].prepared_request.compaction_applied is False
    assert "compaction" not in provider.requests[0].provider_request.metadata


@pytest.mark.asyncio
async def test_summarizer_receives_source_context_and_overrides_provider_summary():
    calls = []

    def fake(request: CompactionRequest) -> CompactionSummary:
        calls.append(request)
        return CompactionSummary(
            summary="Custom model-visible summary.",
            metadata={"summary_id": "summary-1"},
        )

    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(provider, fake, max_parts=2)

    result = await runner.run(session=_old_session(), user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    assert len(calls) == 1
    request = calls[0]
    assert [message.message_id for message in request.messages] == [
        "msg-old-user",
        "msg-old-assistant",
        provider.requests[0].messages[-1].message_id,
    ]
    assert [message.message_id for message in request.compacted_messages] == [
        "msg-old-user",
        "msg-old-assistant",
    ]
    assert [message.parts[0].text for message in request.kept_messages] == [
        "latest request"
    ]
    assert request.metadata["compacted_message_count"] == 2

    provider_request = provider.requests[0].provider_request
    summary_context = provider_request.messages[0].context[0]
    assert summary_context.text == "Custom model-visible summary."
    assert provider_request.metadata["compaction"]["summary_id"] == "summary-1"
    assert provider_request.metadata["compaction"]["summarizer"]["used"] is True
    assert provider_request.metadata["compaction"]["summarizer"]["fallback"] is False


@pytest.mark.asyncio
async def test_async_summarizer_is_awaited():
    calls = []

    async def fake(request: CompactionRequest) -> str:
        calls.append(request)
        return "Async compacted summary."

    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(provider, fake, max_parts=2)

    result = await runner.run(session=_old_session(), user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    assert len(calls) == 1
    summary_context = provider.requests[0].provider_request.messages[0].context[0]
    assert summary_context.text == "Async compacted summary."


@pytest.mark.asyncio
async def test_summarizer_failure_uses_deterministic_fallback_metadata():
    def broken(request: CompactionRequest) -> str:
        raise RuntimeError("summary failed")

    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(provider, broken, max_parts=2)

    result = await runner.run(session=_old_session(), user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    provider_request = provider.requests[0].provider_request
    summary_context = provider_request.messages[0].context[0]
    assert summary_context.text == (
        "Compacted 2 message part(s) from 2 message(s). "
        "Tool call/result pair(s) compacted: 0."
    )
    summarizer = provider_request.metadata["compaction"]["summarizer"]
    assert summarizer["used"] is True
    assert summarizer["fallback"] is True
    assert summarizer["error_type"] == "RuntimeError"
    assert summarizer["summarizer_error"] == "summary failed"


@pytest.mark.asyncio
async def test_tool_pairs_remain_atomic_and_pending_tool_call_is_kept():
    paired_call = ToolCall(
        call_id="call-paired",
        tool_name="search",
        arguments={"query": "runtime"},
    )
    paired_result = ToolResult(
        call_id="call-paired",
        tool_name="search",
        content="paired result",
    )
    pending_call = ToolCall(
        call_id="call-pending",
        tool_name="write_file",
        arguments={"path": "out.txt"},
    )
    messages = [
        Message.from_text("user", "old request", message_id="msg-old"),
        Message(
            role="assistant",
            message_id="msg-paired-call",
            parts=[MessagePart.tool_call_part(paired_call)],
        ),
        Message(
            role="tool",
            message_id="msg-paired-result",
            parts=[MessagePart.tool_result_part(paired_result)],
        ),
        Message(
            role="assistant",
            message_id="msg-pending-call",
            parts=[MessagePart.tool_call_part(pending_call)],
        ),
        Message.from_text("user", "latest request", message_id="msg-latest"),
    ]
    calls = []

    def fake(request: CompactionRequest) -> str:
        calls.append(request)
        return "Tool-aware summary."

    preparation = await maybe_summarize_compaction(
        messages,
        session_id="session-tools",
        budget=ContextBudget(max_parts=3),
        summarizer=fake,
    )
    prepared = prepare_history_for_request(
        messages,
        max_parts=3,
        compaction_summary=preparation.summary,
        compaction_summary_metadata=preparation.summary_metadata,
    )

    assert len(calls) == 1
    assert preparation.result.compacted_tool_pair_count == 1
    compacted_calls, compacted_results = _tool_ids(calls[0].compacted_messages)
    assert compacted_calls == ["call-paired"]
    assert compacted_results == ["call-paired"]
    kept_calls, kept_results = _tool_ids(calls[0].kept_messages)
    assert kept_calls == ["call-pending"]
    assert kept_results == []

    provider_calls = [
        call.call_id for message in prepared.request.messages for call in message.tool_calls
    ]
    provider_results = [
        result.call_id
        for message in prepared.request.messages
        for result in message.tool_results
    ]
    assert provider_calls == ["call-pending"]
    assert provider_results == []
    assert prepared.request.messages[0].context[0].text == "Tool-aware summary."


@pytest.mark.asyncio
async def test_agent_runtime_passes_enabled_compaction_summarizer_to_runner():
    calls = []

    def fake(request: CompactionRequest) -> str:
        calls.append(request)
        return "Facade compacted summary."

    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            max_context_parts=2,
            enable_compaction_summarizer=True,
        ),
        compaction_summarizer=fake,
    )
    runtime.store.create_session(session_id="session-facade-summary")
    runtime.store.append_message(
        "session-facade-summary",
        role=MessageRole.USER,
        parts=[MessagePart.text_part("old request")],
        message_id="msg-old-user",
        status="complete",
    )
    runtime.store.append_message(
        "session-facade-summary",
        role=MessageRole.ASSISTANT,
        parts=[MessagePart.text_part("old answer")],
        message_id="msg-old-assistant",
        status="complete",
    )

    result = await runtime.run("latest request", session_id="session-facade-summary")

    assert result.status == LoopStatus.COMPLETED
    assert len(calls) == 1
    summary_context = _first_summary_context(provider.requests[0].provider_request)
    assert summary_context.text == "Facade compacted summary."


@pytest.mark.asyncio
async def test_agent_runtime_does_not_call_summarizer_when_config_disabled():
    calls = []

    def fake(request: CompactionRequest) -> str:
        calls.append(request)
        return "Should not appear."

    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=1, max_context_parts=2),
        compaction_summarizer=fake,
    )
    runtime.store.create_session(session_id="session-disabled-summary")
    runtime.store.append_message(
        "session-disabled-summary",
        role=MessageRole.USER,
        parts=[MessagePart.text_part("old request")],
        status="complete",
    )
    runtime.store.append_message(
        "session-disabled-summary",
        role=MessageRole.ASSISTANT,
        parts=[MessagePart.text_part("old answer")],
        status="complete",
    )

    result = await runtime.run("latest request", session_id="session-disabled-summary")

    assert result.status == LoopStatus.COMPLETED
    assert calls == []
    summary_context = _first_summary_context(provider.requests[0].provider_request)
    assert summary_context.text != "Should not appear."
    assert "summarizer" not in provider.requests[0].provider_request.metadata["compaction"]


def test_compaction_controller_imports_standalone_with_pythonpath_src():
    code = """
import json
import sys

from efp_runtime.compaction import CompactionController, DeterministicCompactionSummarizer

print(json.dumps({
    "legacy_core_loaded": "src.agents.core" in sys.modules,
    "controller": CompactionController.__name__,
    "summarizer": DeterministicCompactionSummarizer.__name__,
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
        "controller": "CompactionController",
        "summarizer": "DeterministicCompactionSummarizer",
    }


def test_compaction_controller_source_stays_inside_runtime_v2_import_boundary():
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


def _tool_ids(messages):
    calls = []
    results = []
    for message in messages:
        for part in message.parts:
            if part.type.value == "tool_call" and part.tool_call is not None:
                calls.append(part.tool_call.call_id)
            if part.type.value == "tool_result" and part.tool_result is not None:
                results.append(part.tool_result.call_id)
    return calls, results


def _first_summary_context(provider_request):
    for message in provider_request.messages:
        if message.context:
            return message.context[0]
    raise AssertionError("compaction summary context not found")
