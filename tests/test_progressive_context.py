import pytest

from src.runtime import progressive_context
from src.runtime.progressive_context import (
    build_portal_context_preview,
    prepare_progressive_messages,
)


@pytest.mark.asyncio
async def test_prepare_progressive_messages_under_soft_threshold(monkeypatch):
    messages = [
        {"role": "user", "content": "Plan a deployment"},
        {"role": "assistant", "content": "Sure, I can help."},
    ]

    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 300)

    prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s1",
        stage="pre_request",
    )

    assert prepared == messages
    assert state["compaction_level"] == "none"
    assert state["version"] == "context.v1"


@pytest.mark.asyncio
async def test_prepare_progressive_messages_micro_compacts_old_tool_messages(monkeypatch):
    large_tool = "x" * 1800
    messages = [
        {"role": "user", "content": "Start"},
        {"role": "assistant", "content": "Calling tools", "tool_calls": [{"id": "tc-1", "function": {"name": "a", "arguments": "{}"}}]},
        {"role": "tool", "content": large_tool, "tool_call_id": "tc-1", "timestamp": "2026-01-01T00:00:00Z"},
        {"role": "assistant", "content": "Still working"},
        {
            "role": "assistant",
            "content": "More tools",
            "tool_calls": [
                {"id": "tc-2", "function": {"name": "b", "arguments": "{}"}},
                {"id": "tc-3", "function": {"name": "c", "arguments": "{}"}},
                {"id": "tc-4", "function": {"name": "d", "arguments": "{}"}},
                {"id": "tc-5", "function": {"name": "e", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "content": "recent-1", "tool_call_id": "tc-2"},
        {"role": "tool", "content": "recent-2", "tool_call_id": "tc-3"},
        {"role": "tool", "content": "recent-3", "tool_call_id": "tc-4"},
        {"role": "tool", "content": "recent-4", "tool_call_id": "tc-5"},
        {"role": "assistant", "content": "Final"},
    ]

    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)

    def _estimate(msgs):
        if any("[tool_result compacted" in str(m.content) for m in msgs):
            return 750
        return 700

    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", _estimate)

    prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s1",
        stage="pre_request",
    )

    old_tool = next(item for item in prepared if item.get("tool_call_id") == "tc-1")
    assert "[tool_result compacted" in old_tool["content"]
    assert old_tool["tool_call_id"] == "tc-1"
    newest_tool = next(item for item in prepared if item.get("tool_call_id") == "tc-5")
    assert newest_tool["content"] == "recent-4"
    assert state["compaction_level"] == "micro"


@pytest.mark.asyncio
async def test_prepare_progressive_messages_full_compaction_fallback(monkeypatch):
    messages = [
        {"role": "user", "content": "We must finish migration safely."},
        {"role": "assistant", "content": "Acknowledged"},
        {"role": "tool", "content": "y" * 3000, "tool_call_id": "tc-1"},
        {"role": "assistant", "content": "Processing"},
        {"role": "assistant", "content": "Next, validate outputs."},
    ]

    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)

    def _estimate(msgs):
        return 900

    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", _estimate)

    async def _compact_messages(**kwargs):
        return kwargs["messages"][-2:], type("Stats", (), {"summary": "Compacted summary"})()

    monkeypatch.setattr(progressive_context, "compact_messages", _compact_messages)

    prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s1",
        stage="tool_loop",
    )

    assert len(prepared) == 2
    assert state["compaction_level"] == "full"
    assert state["summary"]


def test_build_portal_context_preview_returns_preview_keys_only():
    preview = build_portal_context_preview(
        {
            "compaction_level": "micro",
            "objective": "Do the thing",
            "summary": "A concise summary",
            "next_step": "Continue",
            "constraints": ["must be safe"],
            "version": "context.v1",
        }
    )

    assert preview == {
        "context_compaction_level": "micro",
        "context_objective_preview": "Do the thing",
        "context_summary_preview": "A concise summary",
        "context_next_step_preview": "Continue",
    }
