from __future__ import annotations

from pathlib import Path

import pytest

from efp_runtime import (
    FileSessionStore,
    InMemorySessionStore,
    MessagePart,
    MessagePartType,
    MessageRole,
    ToolCall,
)
from efp_runtime.compaction import CompactionRequest, CompactionSummary
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig


def _runtime(
    *,
    store=None,
    config: RuntimeConfig | None = None,
    summarizer=None,
) -> AgentRuntime:
    return AgentRuntime(
        provider=ScriptedLLMProvider([]),
        store=store,
        config=config,
        compaction_summarizer=summarizer,
    )


def _seed_text_session(store, session_id: str = "session-compact") -> None:
    store.create_session(session_id=session_id)
    store.append_message(
        session_id,
        role=MessageRole.USER,
        parts=[MessagePart.text_part("old request")],
        message_id="msg-old-user",
        status="complete",
    )
    store.append_message(
        session_id,
        role=MessageRole.ASSISTANT,
        parts=[MessagePart.text_part("old answer")],
        message_id="msg-old-assistant",
        status="complete",
    )
    store.append_message(
        session_id,
        role=MessageRole.USER,
        parts=[MessagePart.text_part("latest request")],
        message_id="msg-latest-user",
        status="complete",
    )


def _first_compaction_part(session):
    for message in session.messages:
        for part in message.parts:
            if part.type is MessagePartType.COMPACTION:
                return message, part
    raise AssertionError("compaction part not found")


@pytest.mark.asyncio
async def test_force_compact_persists_summary_and_keeps_latest_message():
    store = InMemorySessionStore()
    _seed_text_session(store)
    runtime = _runtime(store=store)

    session = await runtime.compact_session("session-compact")

    assert [message.role for message in session.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
    ]
    assert [message.message_id for message in session.messages[1:]] == [
        "msg-latest-user"
    ]
    compaction_message, compaction_part = _first_compaction_part(session)
    assert compaction_message.metadata["manual_compaction"] is True
    assert compaction_part.metadata["manual_compaction"] is True
    assert compaction_part.compaction.metadata["manual_compaction"] is True
    assert compaction_part.compaction.metadata["force"] is True
    assert compaction_part.compaction.source_message_ids == [
        "msg-old-user",
        "msg-old-assistant",
    ]
    assert compaction_part.compaction.original_part_count == 2
    assert compaction_part.compaction.original_message_count == 2
    assert compaction_part.compaction.tool_pair_count == 0
    assert compaction_part.text == compaction_part.compaction.summary
    assert [message.message_id for message in store.read_history("session-compact")] == [
        compaction_message.message_id,
        "msg-latest-user",
    ]
    events = runtime.event_bus.history("session-compact")
    assert events[-1].type == "session_compacted"
    assert events[-1].message_id == compaction_message.message_id


@pytest.mark.asyncio
async def test_enabled_summarizer_summary_is_persisted():
    calls: list[CompactionRequest] = []

    async def summarizer(request: CompactionRequest) -> CompactionSummary:
        calls.append(request)
        return CompactionSummary(
            summary="Persisted custom summary.",
            metadata={"summary_id": "summary-manual"},
        )

    store = InMemorySessionStore()
    _seed_text_session(store, "session-summary")
    runtime = _runtime(
        store=store,
        config=RuntimeConfig(enable_compaction_summarizer=True),
        summarizer=summarizer,
    )

    session = await runtime.compact_session(
        "session-summary",
        metadata={"reason": "user_requested"},
    )

    assert len(calls) == 1
    _message, part = _first_compaction_part(session)
    assert part.text == "Persisted custom summary."
    assert part.compaction.summary == "Persisted custom summary."
    assert part.compaction.metadata["summary_id"] == "summary-manual"
    assert part.compaction.metadata["reason"] == "user_requested"
    assert part.compaction.metadata["summarizer"]["used"] is True
    assert part.compaction.metadata["summarizer"]["fallback"] is False


@pytest.mark.asyncio
async def test_disabled_summarizer_uses_deterministic_summary_without_calling_custom():
    calls: list[CompactionRequest] = []

    def summarizer(request: CompactionRequest) -> str:
        calls.append(request)
        return "Should not be stored."

    store = InMemorySessionStore()
    _seed_text_session(store, "session-disabled")
    runtime = _runtime(store=store, summarizer=summarizer)

    session = await runtime.compact_session("session-disabled")

    assert calls == []
    _message, part = _first_compaction_part(session)
    assert part.text == (
        "Compacted 2 message part(s) from 2 message(s). "
        "Tool call/result pair(s) compacted: 0."
    )
    assert part.compaction.metadata["manual_compaction"] is True
    assert "summarizer" not in part.compaction.metadata


@pytest.mark.asyncio
async def test_pending_tool_call_is_preserved():
    store = InMemorySessionStore()
    store.create_session(session_id="session-pending")
    store.append_message(
        "session-pending",
        role=MessageRole.USER,
        parts=[MessagePart.text_part("old request")],
        message_id="msg-old",
        status="complete",
    )
    pending_call = ToolCall(
        call_id="call-pending",
        tool_name="write_file",
        arguments={"path": "created.txt"},
    )
    store.append_message(
        "session-pending",
        role=MessageRole.ASSISTANT,
        parts=[MessagePart.tool_call_part(pending_call)],
        message_id="msg-pending",
        status="pending",
    )
    store.append_message(
        "session-pending",
        role=MessageRole.USER,
        parts=[MessagePart.text_part("latest request")],
        message_id="msg-latest",
        status="complete",
    )
    runtime = _runtime(store=store)

    session = await runtime.compact_session("session-pending")

    assert [message.message_id for message in session.messages] == [
        session.messages[0].message_id,
        "msg-pending",
        "msg-latest",
    ]
    assert session.messages[0].parts[0].type is MessagePartType.COMPACTION
    _message, part = _first_compaction_part(session)
    assert part.compaction.source_message_ids == ["msg-old"]
    pending_calls = [
        item.tool_call.call_id
        for message in session.messages
        for item in message.parts
        if item.type is MessagePartType.TOOL_CALL and item.tool_call is not None
    ]
    assert pending_calls == ["call-pending"]


@pytest.mark.asyncio
async def test_file_store_reload_sees_compacted_history(tmp_path: Path):
    store = FileSessionStore(tmp_path / "store")
    _seed_text_session(store, "session-file")
    runtime = _runtime(store=store)

    await runtime.compact_session("session-file")

    history = FileSessionStore(tmp_path / "store").read_history("session-file")
    assert [message.role for message in history] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
    ]
    assert history[0].parts[0].type is MessagePartType.COMPACTION
    assert history[0].parts[0].compaction.metadata["manual_compaction"] is True
    assert history[1].message_id == "msg-latest-user"
    assert all(message.session_id == "session-file" for message in history)
    assert all(
        part.session_id == "session-file"
        for message in history
        for part in message.parts
    )


@pytest.mark.asyncio
async def test_force_false_under_budget_leaves_history_unchanged():
    store = InMemorySessionStore()
    _seed_text_session(store, "session-under-budget")
    runtime = _runtime(
        store=store,
        config=RuntimeConfig(max_context_parts=10),
    )

    session = await runtime.compact_session("session-under-budget", force=False)

    assert [message.message_id for message in session.messages] == [
        "msg-old-user",
        "msg-old-assistant",
        "msg-latest-user",
    ]
    assert all(
        part.type is not MessagePartType.COMPACTION
        for message in session.messages
        for part in message.parts
    )
    assert runtime.event_bus.history("session-under-budget") == []
