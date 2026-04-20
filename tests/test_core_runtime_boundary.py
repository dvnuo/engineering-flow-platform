import inspect
import re

import pytest

from src.runtime.contracts import make_execution_result


@pytest.mark.asyncio
async def test_execute_tool_via_runtime_bus_propagates_governance_passthrough_hint(monkeypatch):
    from src.agents import core

    async def _fake_execute_tool_or_task_orchestration(**kwargs):
        return make_execution_result(
            request_id="req-1",
            status="success",
            output_payload={"success": True, "content": "ok", "error": None},
            artifacts={"governance": {"tool_result_passthrough_recommended": True}},
        )

    monkeypatch.setattr(core, "execute_tool_or_task_orchestration", _fake_execute_tool_or_task_orchestration)

    result = await core._execute_tool_via_runtime_bus(
        session_id="s-1",
        tool_name="demo_tool",
        args={},
    )

    governance_hint = getattr(result, "_governance", {})
    assert isinstance(governance_hint, dict)
    assert governance_hint.get("tool_result_passthrough_recommended") is True


def test_core_no_longer_directly_calls_should_passthrough_tool_result():
    from src.agents import core

    source = inspect.getsource(core.Agent.process)
    assert "should_passthrough_tool_result(" not in source


def test_read_governance_hint_returns_empty_when_missing():
    from src.agents import core
    from src import ToolResult

    tool_result = ToolResult(success=True, content="ok", error=None)
    assert core._read_governance_hint(tool_result) == {}


def test_read_governance_hint_returns_empty_when_non_dict():
    from src.agents import core
    from src import ToolResult

    tool_result = ToolResult(success=True, content="ok", error=None)
    setattr(tool_result, "_governance", "not-a-dict")
    assert core._read_governance_hint(tool_result) == {}


def test_attach_governance_hint_preserves_tool_result_fields():
    from src.agents import core
    from src import ToolResult

    tool_result = ToolResult(success=False, content="body", error="err")
    returned = core._attach_governance_hint(tool_result, {"tool_result_passthrough_recommended": True})

    assert returned is tool_result
    assert returned.success is False
    assert returned.content == "body"
    assert returned.error == "err"
    assert core._read_governance_hint(returned).get("tool_result_passthrough_recommended") is True


def test_agent_process_source_prefers_self_model_in_multiple_paths():
    from src.agents import core

    source = inspect.getsource(core.Agent.process)
    expected = 'self.model or config.llm.get("model", "gpt-5-mini")'
    assert source.count(expected) >= 2


def test_tool_feedback_text_preserves_short_text():
    from src.agents import core

    value = "short tool output"
    assert core._tool_feedback_text(value) == value
    assert "chars hidden" not in core._tool_feedback_text(value)


def test_tool_feedback_text_truncates_long_text_with_count_at_default_8000():
    from src.agents import core

    long_value = "A" * 9005
    output = core._tool_feedback_text(long_value)

    assert output.startswith("A" * 8000)
    assert "1005 chars hidden" in output
    assert len(output) < len(long_value)


@pytest.mark.parametrize("tool_name", ["jira_get_issue", "confluence_get_page"])
def test_tool_feedback_text_for_large_jira_confluence_is_bounded_with_ref(tool_name):
    from src.agents import core
    from src.context_blob_store import read_ref

    long_value = "A" * 20000
    output = core._tool_feedback_text_for_tool(tool_name, long_value, session_id="s-core")
    assert "context_ref: ctx://context/" in output
    assert "original_chars: 20000" in output
    assert "full_content_available: true" in output
    assert len(output) < len(long_value)
    ref_match = re.search(r"context_ref:\s*(ctx://context/[^\s]+)", output)
    assert ref_match
    assert read_ref(ref_match.group(1), session_id="s-core", max_chars=22000) == long_value


def test_tool_feedback_text_for_non_jira_confluence_uses_default_8000_limit():
    from src.agents import core

    long_value = "A" * 9005
    output = core._tool_feedback_text_for_tool("github_get_pull_request", long_value)

    assert output.startswith("A" * 8000)
    assert "1005 chars hidden" in output
    assert len(output) < len(long_value)


def test_large_source_feedback_prefix_policy_is_prefix_based():
    from src.agents import core

    assert "jira_" in core.LARGE_SOURCE_TOOL_PREFIXES
    assert "confluence_" in core.LARGE_SOURCE_TOOL_PREFIXES


def test_tool_feedback_text_allows_explicit_unbounded_max_length():
    from src.agents import core

    value = "A" * 20000
    assert core._tool_feedback_text(value, max_length=None) == value
    assert core._tool_feedback_text(value, max_length=0) == value


def test_tool_feedback_text_for_short_jira_confluence_keeps_full_text():
    from src.agents import core

    value = "short jira body"
    output = core._tool_feedback_text_for_tool("jira_get_issue", value, session_id="s-core")
    assert output == value


def test_large_source_feedback_projection_is_idempotent():
    from src.agents import core
    from src.context_blob_store import read_ref

    raw = "# MMGFX-1\n## Description\nA\n" + ("x" * 20000)
    first = core._tool_feedback_text_for_tool("jira_get_issue", raw, session_id="s1")
    second = core._tool_feedback_text_for_tool("jira_get_issue", first, session_id="s1")
    assert second == first
    assert second.count("[large source tool result projected]") == 1
    ref = re.search(r"context_ref:\s*(ctx://context/[^\s]+)", first).group(1)
    restored = read_ref(ref, session_id="s1", max_chars=26000)
    assert restored == raw


def test_jira_envelope_prioritizes_acceptance_criteria_even_when_late():
    from src.agents import core

    jira_text = (
        "# MMGFX-9: Title\n**Status:** Open\n## Description\nShort desc\n"
        + ("noise\n" * 4000)
        + "## Acceptance Criteria\n- Must validate AC path\n- Another AC\n"
    )
    output = core._tool_feedback_text_for_tool("jira_get_issue", jira_text, session_id="s-ac")
    assert "Acceptance Criteria" in output
    assert "ctx://context/" in output


def test_agent_process_source_uses_per_tool_feedback_policy_for_all_tool_feedback_paths():
    from src.agents import core

    process_source = inspect.getsource(core.Agent.process)
    module_source = inspect.getsource(core)

    # Agent.process should cover deny / short-circuit / normal tool result feedback.
    assert process_source.count("_tool_feedback_text_for_tool(") >= 3
    # Module-level coverage also includes skill mode function_call_output feedback.
    assert module_source.count("_tool_feedback_text_for_tool(") >= 4


def test_to_input_items_source_uses_per_tool_feedback_policy():
    from src.agents import core

    module_source = inspect.getsource(core)
    assert '"output": _tool_feedback_text(content) if content else ""' not in module_source
    assert "_tool_feedback_text_for_tool(" in module_source
    assert "tool_names_by_call_id" in module_source
    assert "build_responses_input_items(" in module_source


def test_to_input_items_path_has_projected_feedback_guard():
    from src.agents import core

    module_source = inspect.getsource(core)
    assert "_is_projected_large_source_feedback" in module_source


def test_projected_marker_without_ctx_ref_is_still_bounded_for_non_large_source():
    from src.agents import core

    malicious = "[old assistant output compacted | ref=none]\n" + ("x" * 50000)
    output = core._tool_feedback_text_for_tool("github_get_pull_request", malicious)
    assert len(output) < len(malicious)
    assert "chars hidden" in output


def test_real_projected_marker_with_ctx_ref_passes_through():
    from src.agents import core

    projected = (
        "[old assistant output compacted | original_chars=20000 | ref=ctx://context/s/k/aaaaaaaaaaaa]\n"
        "Summary:\nhello"
    )
    assert core._tool_feedback_text_for_tool("github_get_pull_request", projected) == projected


def test_build_responses_input_items_mobilex_shape_projects_large_assistant_and_jira_content():
    from src.agents import core
    from src.context_blob_store import read_ref

    long_gherkin = "Feature: MobileX\n" + ("Scenario: Long\nGiven step\nWhen step\nThen step\n" * 500)
    long_jira = (
        "# MMGFX-123: Generator\n**Status:** Open\n## Description\n"
        + ("noise\n" * 3500)
        + "## Acceptance Criteria\n- AC one\n- AC two\n"
    )
    messages = [
        {"role": "user", "content": "/mobilex-test-generator build tests"},
        {"role": "assistant", "content": long_gherkin, "tool_calls": [
            {"id": "call_1", "function": {"name": "jira_get_issue", "arguments": "{\"issue_key\":\"MMGFX-123\"}"}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "tool_name": "jira_get_issue", "content": long_jira},
    ]

    items = core.build_responses_input_items(messages, session_id="s-mobilex")
    assistant_item = next(item for item in items if item.get("role") == "assistant")
    tool_output_item = next(item for item in items if item.get("type") == "function_call_output")
    function_call = next(item for item in items if item.get("type") == "function_call")

    assert function_call["call_id"] == "call_1"
    assert tool_output_item["call_id"] == "call_1"
    assert assistant_item["content"] != long_gherkin
    assert tool_output_item["output"] != long_jira
    assert "ctx://context/" in assistant_item["content"]
    assert "context_ref: ctx://context/" in tool_output_item["output"]
    assert "Acceptance Criteria" in tool_output_item["output"]
    jira_ref = re.search(r"context_ref:\s*(ctx://context/[^\s]+)", tool_output_item["output"]).group(1)
    assert read_ref(jira_ref, session_id="s-mobilex", max_chars=30000) == long_jira


def test_degrade_projected_context_sources_in_responses_input_items_preserves_call_id():
    from src.agents import core

    large_projected = (
        "[large source tool result projected]\n"
        "context_ref: ctx://context/s/k/aaaaaaaaaaaa\n"
        + ("Z" * 6000)
    )
    items = [{"type": "function_call_output", "call_id": "abc123", "output": large_projected}]
    degraded = core.degrade_projected_context_sources_in_responses_input_items(items, max_envelope_chars=500)
    assert degraded[0]["call_id"] == "abc123"
    assert len(degraded[0]["output"]) < len(large_projected)


def test_tool_loop_budget_updates_include_request_estimation_fields():
    from src.agents import core

    source = inspect.getsource(core.Agent.process)
    assert 'budget_state["request_estimated_tokens"]' in source
    assert 'budget_state["request_over_budget"]' in source
    assert 'emit_context_snapshot("tool_loop_budget"' in source


def test_tool_loop_over_budget_path_applies_real_degradation_before_guard_prompt():
    from src.agents import core

    source = inspect.getsource(core.Agent.process)
    assert "degrade_projected_context_sources(loop_messages)" in source


def test_continue_skill_mode_source_uses_budget_estimation_and_skill_generation_budget():
    from src.agents import core

    source = inspect.getsource(core.Agent._continue_skill_mode)
    assert "estimate_llm_request_tokens(" in source
    assert 'resolve_prompt_budget(stage="skill_generation"' in source
    assert "degrade_projected_context_sources_in_responses_input_items(input_items)" in source


def test_context_budget_exceeded_error_contains_safe_budget_fields():
    from src.agents import core

    err = core._build_context_budget_exceeded_error(
        request_estimated_tokens=12000,
        loop_budget={
            "prompt_budget_tokens": 10000,
            "reserved_output_tokens": 2000,
            "safety_margin_tokens": 500,
            "max_prompt_tokens": 10000,
            "max_output_tokens": 4000,
        },
        stage="tool_loop",
    )
    details = err["details"]
    for key in (
        "request_estimated_tokens",
        "prompt_budget_tokens",
        "reserved_output_tokens",
        "safety_margin_tokens",
        "max_prompt_tokens",
        "max_output_tokens",
        "request_over_budget",
        "stage",
        "suggestion",
    ):
        assert key in details
    for unsafe_key in ("prompt", "payload", "input_items", "tools", "source_docs", "raw_model_response", "api_key"):
        assert unsafe_key not in details


def test_merge_request_budget_into_context_state_merges_safe_fields():
    from src.agents import core

    merged = core._merge_request_budget_into_context_state(
        {"budget": {"existing": 1}},
        {"request_estimated_tokens": 123, "request_over_budget": True, "ignored_key": "x"},
    )
    assert merged["budget"]["existing"] == 1
    assert merged["budget"]["request_estimated_tokens"] == 123
    assert merged["budget"]["request_over_budget"] is True
    assert "ignored_key" not in merged["budget"]


def test_agent_process_source_attaches_runtime_events_for_early_budget_and_llm_errors():
    from src.agents import core

    source = inspect.getsource(core.Agent.process)
    assert "return attach_runtime_events(error_response)" in source
    assert "_merge_budget_into_error_details(error_response, latest_request_budget)" in source


def test_continue_skill_mode_source_merges_budget_into_llm_errors():
    from src.agents import core

    source = inspect.getsource(core.Agent._continue_skill_mode)
    assert "_merge_budget_into_error_details(error_response, skill_request_budget)" in source


def test_is_meaningful_context_state_rules():
    from src.agents import core

    assert core._is_meaningful_context_state({}) is False
    assert core._is_meaningful_context_state({"objective": ""}) is False
    assert core._is_meaningful_context_state({"objective": "Ship final snapshot"}) is True
    assert core._is_meaningful_context_state({"constraints": [""]}) is False
    assert core._is_meaningful_context_state({"constraints": ["must preserve context"]}) is True
    assert core._is_meaningful_context_state({"budget": {"usage_percent": 42.0}}) is True


def test_build_terminal_context_snapshot_event_builds_standard_event():
    from src.agents import core

    event = core._build_terminal_context_snapshot_event(
        context_state={"summary": "Final summary", "budget": {"usage_percent": 42.0}},
        session_id="s-1",
        agent_id="agent-1",
        request_id="req-1",
        status="completed",
    )

    assert event is not None
    assert event["type"] == "context_snapshot"
    assert event["event_type"] == "context_snapshot"
    assert event["state"] == "completed"
    assert event["session_id"] == "s-1"
    assert event["request_id"] == "req-1"
    assert event["agent_id"] == "agent-1"
    assert event["data"]["stage"] == "post_turn"
    assert event["data"]["terminal"] is True
    assert event["data"]["context_state"]["summary"] == "Final summary"
    assert event["data"]["budget"]["usage_percent"] == 42.0
    assert event["detail_payload"]["terminal"] is True


@pytest.mark.asyncio
async def test_run_chat_execution_appends_terminal_context_snapshot(monkeypatch):
    from src.agents import core

    class _FakeAgent:
        model = "gpt-5-mini"
        agent_id = "agent-1"

        async def process(self, **kwargs):
            return {
                "response": "ok",
                "runtime_events": [
                    {
                        "type": "execution.started",
                        "event_type": "execution.started",
                        "data": {"message": "started"},
                    }
                ],
            }

    async def _fake_apply_progressive_context_after_turn(*, session_id, model):
        assert session_id == "s-1"
        assert model == "gpt-5-mini"
        return {"summary": "Final context", "budget": {"usage_percent": 12.5}}

    monkeypatch.setattr(core, "apply_progressive_context_after_turn", _fake_apply_progressive_context_after_turn)

    result = await core.run_chat_execution(
        _FakeAgent(),
        message="hello",
        session_id="s-1",
        request_id="req-1",
    )

    assert result["context_state"]["summary"] == "Final context"
    assert result["request_id"] == "req-1"
    assert result["runtime_events"][0]["type"] == "execution.started"
    terminal_events = [
        event
        for event in result["runtime_events"]
        if event.get("type") == "context_snapshot"
        and (event.get("data") or {}).get("stage") == "post_turn"
        and (event.get("data") or {}).get("terminal") is True
    ]
    assert len(terminal_events) == 1
    assert terminal_events[0]["data"]["context_state"]["summary"] == "Final context"


@pytest.mark.asyncio
async def test_run_chat_execution_does_not_duplicate_existing_terminal_context_snapshot(monkeypatch):
    from src.agents import core

    class _FakeAgent:
        model = "gpt-5-mini"
        agent_id = "agent-1"

        async def process(self, **kwargs):
            return {
                "response": "ok",
                "runtime_events": [
                    {
                        "type": "context_snapshot",
                        "event_type": "context_snapshot",
                        "data": {
                            "stage": "post_turn",
                            "terminal": True,
                            "context_state": {"summary": "Existing final context"},
                        },
                        "detail_payload": {
                            "stage": "post_turn",
                            "terminal": True,
                        },
                    }
                ],
            }

    async def _fake_apply_progressive_context_after_turn(*, session_id, model):
        return {"summary": "New final context", "budget": {"usage_percent": 12.5}}

    monkeypatch.setattr(core, "apply_progressive_context_after_turn", _fake_apply_progressive_context_after_turn)

    result = await core.run_chat_execution(
        _FakeAgent(),
        message="hello",
        session_id="s-1",
    )

    terminal_events = [
        event
        for event in result["runtime_events"]
        if event.get("type") == "context_snapshot"
        and (event.get("data") or {}).get("stage") == "post_turn"
        and (event.get("data") or {}).get("terminal") is True
    ]
    assert len(terminal_events) == 1
    assert terminal_events[0]["data"]["context_state"]["summary"] == "Existing final context"
    assert result["context_state"]["summary"] == "New final context"


@pytest.mark.asyncio
async def test_run_chat_execution_does_not_append_terminal_context_snapshot_for_empty_context(monkeypatch):
    from src.agents import core

    class _FakeAgent:
        model = "gpt-5-mini"
        agent_id = "agent-1"

        async def process(self, **kwargs):
            return {
                "response": "ok",
                "runtime_events": [
                    {
                        "type": "execution.started",
                        "event_type": "execution.started",
                        "data": {"message": "started"},
                    }
                ],
            }

    async def _fake_apply_progressive_context_after_turn(*, session_id, model):
        return {}

    monkeypatch.setattr(core, "apply_progressive_context_after_turn", _fake_apply_progressive_context_after_turn)

    result = await core.run_chat_execution(
        _FakeAgent(),
        message="hello",
        session_id="s-1",
    )

    assert result["context_state"] == {}
    terminal_events = [
        event
        for event in result["runtime_events"]
        if event.get("type") == "context_snapshot"
        and (event.get("data") or {}).get("stage") == "post_turn"
        and (event.get("data") or {}).get("terminal") is True
    ]
    assert terminal_events == []


@pytest.mark.asyncio
async def test_run_chat_execution_uses_result_request_id_fallback_for_terminal_event(monkeypatch):
    from src.agents import core

    class _FakeAgent:
        model = "gpt-5-mini"
        agent_id = "agent-1"

        async def process(self, **kwargs):
            assert kwargs.get("request_id") is None
            return {
                "request_id": "req-from-result",
                "response": "ok",
                "runtime_events": [],
            }

    async def _fake_apply_progressive_context_after_turn(*, session_id, model):
        return {"summary": "Final context"}

    monkeypatch.setattr(core, "apply_progressive_context_after_turn", _fake_apply_progressive_context_after_turn)

    result = await core.run_chat_execution(
        _FakeAgent(),
        message="hello",
        session_id="s-1",
    )

    assert result["request_id"] == "req-from-result"
    terminal_events = [
        event
        for event in result["runtime_events"]
        if event.get("type") == "context_snapshot"
        and (event.get("data") or {}).get("stage") == "post_turn"
        and (event.get("data") or {}).get("terminal") is True
    ]
    assert len(terminal_events) == 1
    assert terminal_events[0]["request_id"] == "req-from-result"
    assert terminal_events[0]["data"]["request_id"] == "req-from-result"
    assert terminal_events[0]["detail_payload"]["request_id"] == "req-from-result"


@pytest.mark.asyncio
async def test_run_chat_execution_appends_when_existing_terminal_snapshot_has_no_meaningful_context(monkeypatch):
    from src.agents import core

    class _FakeAgent:
        model = "gpt-5-mini"
        agent_id = "agent-1"

        async def process(self, **kwargs):
            return {
                "response": "ok",
                "runtime_events": [
                    {
                        "type": "context_snapshot",
                        "event_type": "context_snapshot",
                        "data": {
                            "stage": "post_turn",
                            "terminal": True,
                            "context_state": {},
                        },
                        "detail_payload": {
                            "stage": "post_turn",
                            "terminal": True,
                        },
                    }
                ],
            }

    async def _fake_apply_progressive_context_after_turn(*, session_id, model):
        return {
            "summary": "Real final context",
            "next_step": "Render final snapshot",
            "budget": {"usage_percent": 20.0},
        }

    monkeypatch.setattr(core, "apply_progressive_context_after_turn", _fake_apply_progressive_context_after_turn)

    result = await core.run_chat_execution(
        _FakeAgent(),
        message="hello",
        session_id="s-1",
        request_id="req-1",
    )

    terminal_events = [
        event
        for event in result["runtime_events"]
        if event.get("type") == "context_snapshot"
        and (event.get("data") or {}).get("stage") == "post_turn"
        and (event.get("data") or {}).get("terminal") is True
    ]

    assert len(terminal_events) == 2
    assert terminal_events[-1]["data"]["context_state"]["summary"] == "Real final context"
    assert terminal_events[-1]["data"]["request_id"] == "req-1"
