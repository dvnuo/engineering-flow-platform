import pytest

from src.runtime import progressive_context
from src.runtime.progressive_context import (
    build_portal_context_preview,
    degrade_projected_context_sources,
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
async def test_prepare_progressive_messages_adds_budget_fields(monkeypatch):
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 400)

    _prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-budget-fields",
        stage="pre_request",
    )

    assert state["budget"]["context_window_tokens"] == 1000
    assert state["budget"]["estimated_tokens"] == 400
    assert state["budget"]["usage_percent"] == 40.0
    assert state["budget"]["prepared_usage_percent"] == 40.0
    assert state["budget"]["soft_threshold_tokens"] == 650
    assert state["budget"]["hard_threshold_tokens"] == 700
    assert state["budget"]["tokens_until_soft_threshold"] == 250
    assert state["budget"]["tokens_until_hard_threshold"] == 300
    assert state["budget"]["prompt_budget_tokens"] == 700
    assert state["budget"]["reserved_output_tokens"] == 250
    assert state["budget"]["next_compaction_action"] == "none"
    assert "No compaction planned" in state["budget"]["next_pruning_policy"]


@pytest.mark.asyncio
async def test_prepare_progressive_messages_budget_marks_approaching_soft_threshold(monkeypatch):
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 610)

    _prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-budget-soft",
        stage="pre_request",
    )

    assert state["budget"]["next_compaction_action"] == "approaching_micro_compaction"


@pytest.mark.asyncio
async def test_context_budget_includes_next_pruning_policy(monkeypatch):
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 400)

    _prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-pruning-none",
        stage="pre_request",
    )

    assert state["budget"]["next_compaction_action"] == "none"
    assert "No compaction planned" in state["budget"]["next_pruning_policy"]

    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 610)
    _prepared, approaching_state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-pruning-approaching",
        stage="pre_request",
    )
    assert approaching_state["budget"]["next_compaction_action"] == "approaching_micro_compaction"
    assert "Approaching micro-compaction" in approaching_state["budget"]["next_pruning_policy"]


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
    assert state["compaction_level"] in {"micro", "full", "projection_micro"}


@pytest.mark.asyncio
async def test_prepare_progressive_messages_full_compaction_final_result_respects_hard_threshold(monkeypatch):
    messages = [
        {"role": "user", "content": "Need staged migration plan."},
        {"role": "assistant", "content": "Acknowledged and starting analysis."},
        {"role": "assistant", "content": "a" * 900},
        {"role": "tool", "content": "b" * 900, "tool_call_id": "tc-99"},
        {"role": "assistant", "content": "c" * 900},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 1000)

    def _estimate(msgs):
        return sum(len(str(m.content or "")) for m in msgs)

    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", _estimate)

    async def _compact_messages(**kwargs):
        return kwargs["messages"], type("Stats", (), {"summary": "placeholder"})()

    monkeypatch.setattr(progressive_context, "compact_messages", _compact_messages)

    prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-budget",
        stage="tool_loop",
    )

    assert state["compaction_level"] == "full"
    assert prepared[0]["role"] == "system"
    assert _estimate(progressive_context._to_agent_messages(prepared)) <= 750
    assert state["history_compacted_to_count"] == len(prepared)


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


def test_build_portal_context_preview_includes_request_budget_fields():
    preview = build_portal_context_preview(
        {
            "compaction_level": "projection",
            "summary": "s",
            "budget": {
                "prepared_usage_percent": 12.0,
                "prompt_budget_tokens": 32000,
                "request_estimated_tokens": 40000,
                "reserved_output_tokens": 16000,
                "safety_margin_tokens": 4000,
                "max_prompt_tokens": 32000,
                "max_output_tokens": 64000,
                "projection_chars_saved": 12345,
                "projected_old_assistant_messages": 2,
                "projected_old_tool_messages": 3,
                "context_blob_refs_created": ["ctx://context/a", "ctx://context/b"],
                "request_over_budget": True,
                "request_budget_stage": "skill_finalizer",
            },
        }
    )
    import json

    assert preview["context_prompt_budget_tokens"] == 32000
    assert preview["context_request_estimated_tokens"] == 40000
    assert preview["context_reserved_output_tokens"] == 16000
    assert preview["context_safety_margin_tokens"] == 4000
    assert preview["context_max_prompt_tokens"] == 32000
    assert preview["context_max_output_tokens"] == 64000
    assert preview["context_projection_chars_saved"] == 12345
    assert preview["context_projected_old_assistant_messages"] == 2
    assert preview["context_projected_old_tool_messages"] == 3
    assert preview["context_context_blob_refs_created"] == 2
    assert preview["context_request_budget_stage"] == "skill_finalizer"
    assert preview["context_request_over_budget"] is True
    assert "ctx://context/a" not in json.dumps(preview)


def test_progressive_context_dict_roundtrip_preserves_tool_name():
    messages = [
        {
            "role": "tool",
            "content": "full jira content",
            "tool_call_id": "call_1",
            "tool_name": "jira_get_issue",
        }
    ]

    agent_messages = progressive_context._to_agent_messages(messages)
    restored = progressive_context._to_dict_messages(agent_messages)

    assert restored[0]["tool_name"] == "jira_get_issue"
    assert restored[0]["tool_call_id"] == "call_1"


def test_resolve_prompt_budget_caps_large_context_window(monkeypatch):
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 200000)
    budget = progressive_context.resolve_prompt_budget(stage="tool_loop", model="gpt-5-mini")
    assert budget["prompt_budget_tokens"] == 32000
    assert budget["reserved_output_tokens"] == 16000


@pytest.mark.asyncio
async def test_projection_runs_before_threshold_checks_and_compacts_old_assistant(monkeypatch):
    old = "Feature: Big\n" + ("\nScenario: very long\nGiven x" * 4000)
    messages = [
        {"role": "assistant", "content": old},
        {"role": "user", "content": "latest"},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 200000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 100)

    prepared, state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-proj",
        stage="tool_loop",
        recent_count=1,
    )
    assert prepared != messages
    assert "ctx://context/" in prepared[0]["content"]
    assert state["budget"]["projected_old_assistant_messages"] >= 1


@pytest.mark.asyncio
async def test_apply_progressive_context_after_turn_does_not_overwrite_history(monkeypatch):
    session = {
        "history": [{"role": "assistant", "content": "x" * 20000}, {"role": "user", "content": "next"}],
        "metadata": {},
    }

    async def _get_session(_session_id):
        return session

    saved = {}

    async def _set_context_state(_session_id, state):
        saved["state"] = state

    monkeypatch.setattr(progressive_context.session_manager, "get_session", _get_session)
    monkeypatch.setattr(progressive_context.session_manager, "set_context_state", _set_context_state)

    result = await progressive_context.apply_progressive_context_after_turn(session_id="s1", model="gpt-5-mini")
    assert session["history"][0]["content"] == "x" * 20000
    assert isinstance(result, dict)
    assert "state" in saved


@pytest.mark.asyncio
async def test_projects_large_assistant_with_tool_calls_even_when_recent(monkeypatch):
    huge = "```gherkin\n" + ("\nScenario: S\nGiven x\nWhen y\nThen z" * 2500)
    messages = [
        {"role": "user", "content": "generate tests"},
        {
            "role": "assistant",
            "content": huge,
            "tool_calls": [{"id": "call_1", "function": {"name": "jira_get_issue_by_url", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "tool_name": "jira_get_issue_by_url", "content": "short result"},
    ]

    async def _get_context_state(_session_id):
        return {}

    monkeypatch.setattr(progressive_context.session_manager, "get_context_state", _get_context_state)
    monkeypatch.setattr(progressive_context, "resolve_context_window_tokens", lambda model: 200000)
    monkeypatch.setattr(progressive_context, "estimate_messages_tokens", lambda msgs: 100)

    prepared, _state = await prepare_progressive_messages(
        messages=messages,
        model="gpt-5-mini",
        session_id="s-assistant-toolcalls",
        stage="tool_loop",
        recent_count=5,
    )
    assistant = next(m for m in prepared if m["role"] == "assistant")
    assert "[assistant tool-call content compacted" in assistant["content"]
    assert "ctx://context/" in assistant["content"]
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "jira_get_issue_by_url"


def test_degrade_projected_context_sources_keeps_ref_and_call_id():
    messages = [
        {"role": "user", "content": "u"},
        {
            "role": "tool",
            "tool_call_id": "call_9",
            "content": "[large source tool result projected]\ntool_name: jira_get_issue\nkind: jira_issue\ncontext_ref: ctx://context/s/jira_issue/aaaaaaaaaaaa\noriginal_chars: 12000\nmodel_view_chars: 8000\nfull_content_available: true\nsection_map:\n- A\n- B\npreview:\n" + ("x" * 5000) + "\nTo read more, call: context_read_ref(ref=\"ctx://context/s/jira_issue/aaaaaaaaaaaa\", section=\"raw\", max_chars=6000)",
        },
    ]
    degraded = degrade_projected_context_sources(messages, max_envelope_chars=600)
    tool = degraded[1]
    assert tool["tool_call_id"] == "call_9"
    assert "context_ref: ctx://context/s/jira_issue/aaaaaaaaaaaa" in tool["content"]
    assert "context_read_ref(" in tool["content"]
