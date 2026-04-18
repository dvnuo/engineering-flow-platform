import pytest

from src.runtime import progressive_context
from src.runtime.progressive_context import (
    build_portal_context_preview,
    prepare_progressive_messages,
)


@pytest.mark.asyncio
async def test_prepare_progressive_messages_merges_prior_objective_when_current_is_missing(monkeypatch):
    messages = [{"role": "assistant", "content": "I will continue execution."}]

    async def _get_context_state(_session_id):
        return {
            "objective": "Build a resilient deployment pipeline with rollback guarantees",
            "constraints": ["must be idempotent"],
            "next_step": "Verify migrations",
        }

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 200)

    prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-prior",
        stage="pre_request",
    )

    assert prepared == messages
    assert state["objective"].startswith("Build a resilient deployment pipeline")
    assert "must be idempotent" in state["constraints"]


@pytest.mark.asyncio
async def test_prepare_progressive_messages_stage_aware_thresholds(monkeypatch):
    messages = [
        {"role": "user", "content": "Do this"},
        {"role": "assistant", "content": "Acknowledged"},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 620)

    pre_messages, pre_state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-stage",
        stage="pre_request",
    )
    loop_messages, loop_state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-stage",
        stage="tool_loop",
    )

    assert pre_state["compaction_level"] == "none"
    assert pre_messages == messages
    assert loop_state["compaction_level"] == "micro"


@pytest.mark.asyncio
async def test_prepare_progressive_messages_full_compaction_adds_structured_summary(monkeypatch):
    messages = [
        {"role": "user", "content": "We must finish migration safely."},
        {"role": "assistant", "content": "Decision: we will run phased rollout."},
        {"role": "tool", "content": "y" * 3000, "tool_call_id": "tc-1"},
        {"role": "assistant", "content": "Next, validate outputs."},
        {"role": "assistant", "content": "Pending: verify canary metrics."},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 900)

    async def _compact_messages(**kwargs):
        return kwargs["messages"][-3:], type("Stats", (), {"summary": "placeholder"})()

    monkeypatch.setattr(progressive_context, "compact_messages", _compact_messages)

    prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-full",
        stage="tool_loop",
    )

    assert state["compaction_level"] == "full"
    assert prepared[0]["role"] == "system"
    assert str(prepared[0]["content"]).startswith("Context summary:")
    assert state["summary_source"] == "full"
    assert state["history_compacted_from_count"] == len(messages)
    assert state["history_compacted_to_count"] == len(prepared)
    assert state["recovery_context_message"].startswith("Recovered context:")


@pytest.mark.asyncio
async def test_prepare_progressive_messages_full_compaction_keeps_earliest_objective(monkeypatch):
    messages = [
        {"role": "user", "content": "Primary objective: migrate the billing service with zero downtime."},
        {"role": "assistant", "content": "Understood."},
        {"role": "tool", "content": "z" * 5000, "tool_call_id": "tc-1"},
        {"role": "assistant", "content": "Working on it."},
        {"role": "assistant", "content": "Next, validate."},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 900)

    async def _compact_messages(**kwargs):
        # Drop earliest user message to mimic aggressive compaction output subset
        return kwargs["messages"][-2:], type("Stats", (), {"summary": "placeholder"})()

    monkeypatch.setattr(progressive_context, "compact_messages", _compact_messages)

    prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-obj",
        stage="tool_loop",
    )

    assert prepared[0]["role"] == "system"
    assert "migrate the billing service" in state["objective"].lower()


@pytest.mark.asyncio
async def test_prepare_progressive_messages_preserves_tool_chain_consistency(monkeypatch):
    large_tool = "x" * 1800
    messages = [
        {"role": "user", "content": "Start"},
        {
            "role": "assistant",
            "content": "Calling tools",
            "tool_calls": [{"id": "tc-1", "function": {"name": "a", "arguments": "{}"}}],
        },
        {"role": "tool", "content": large_tool, "tool_call_id": "tc-1", "timestamp": "2026-01-01T00:00:00Z"},
        {"role": "assistant", "content": "Next"},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)

    def _estimate(msgs):
        if any("[tool_result compacted" in str(m.content) for m in msgs):
            return 700
        return 700

    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", _estimate)

    prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-tool-chain",
        stage="tool_loop",
    )

    tool_ids = [item.get("tool_call_id") for item in prepared if item.get("role") == "tool"]
    assert "tc-1" in tool_ids
    assert any(item.get("tool_calls") for item in prepared if item.get("role") == "assistant")
    assert state["compaction_level"] in {"micro", "full"}


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
