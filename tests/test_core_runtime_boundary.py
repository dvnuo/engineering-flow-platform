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


def test_core_safe_int_handles_none_for_max_chat_output_chars():
    from src.agents import core

    assert core._safe_int(None, 8000) == 8000
    assert core._safe_int("", 8000) == 8000
    assert core._safe_int("7000", 8000) == 7000


def test_agent_process_passes_raw_max_chat_output_chars_to_output_controller():
    from src.agents import core

    source = inspect.getsource(core.Agent.process)
    assert "raw_max_chat =" in source
    assert "max_chat_output_chars=raw_max_chat" in source


def test_core_paths_use_effective_max_tokens_helper():
    from src.agents import core

    module_source = inspect.getsource(core)
    assert module_source.count("_resolve_effective_max_tokens(") >= 3


def test_session_compactor_default_uses_model_prompt_budget(monkeypatch):
    from src.config import config
    from src.sessions.pruning import SessionCompactor

    monkeypatch.setitem(
        config._config,
        "llm",
        {
            "model": "gpt-5.4-mini",
            "model_limits": {
                "gpt-5.4-mini": {
                    "max_context_window_tokens": 400000,
                    "max_prompt_tokens": 272000,
                    "max_output_tokens": 128000,
                }
            },
        },
    )
    compactor = SessionCompactor()
    assert compactor.max_context_tokens >= 264000


def test_resolve_effective_max_tokens_ignores_legacy_lower_limit(monkeypatch):
    from src.agents import core
    from src.config import config

    monkeypatch.setitem(
        config._config,
        "llm",
        {"model": "gpt-5.4-mini", "max_tokens": 64000, "allow_lower_max_tokens_than_model_limit": False},
    )
    effective, diag = core._resolve_effective_max_tokens(128000)
    assert effective == 128000
    assert diag["configured_max_tokens"] == 64000
    assert diag["legacy_max_tokens_ignored"] is True


def test_resolve_effective_max_tokens_accepts_explicit_lower_override(monkeypatch):
    from src.agents import core
    from src.config import config

    monkeypatch.setitem(
        config._config,
        "llm",
        {"model": "gpt-5.4-mini", "max_tokens": 64000, "allow_lower_max_tokens_than_model_limit": True},
    )
    effective, diag = core._resolve_effective_max_tokens(128000)
    assert effective == 64000
    assert diag["configured_max_tokens"] == 64000
    assert diag["legacy_max_tokens_ignored"] is False


def test_resolve_effective_max_tokens_keeps_model_cap_for_smaller_models(monkeypatch):
    from src.agents import core
    from src.config import config

    monkeypatch.setitem(
        config._config,
        "llm",
        {"model": "gpt-4o", "max_tokens": 128000, "allow_lower_max_tokens_than_model_limit": False},
    )
    effective, diag = core._resolve_effective_max_tokens(16384)
    assert effective == 16384
    assert diag["legacy_max_tokens_ignored"] is False


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


def test_agent_process_source_filters_messages_excluded_from_model_context():
    from src.agents import core

    process_source = inspect.getsource(core.Agent.process)
    assert 'metadata = msg.get("metadata") if isinstance(msg, dict) else None' in process_source
    assert 'metadata.get("exclude_from_model_context")' in process_source
    assert 'msg.get("exclude_from_model_context")' in process_source


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


def test_assistant_skill_projected_marker_is_recognized_and_idempotent():
    from src.agents import core

    projected = (
        "[assistant skill content compacted | original_chars=20000 | ref=ctx://context/s/k/aaaaaaaaaaaa]\n"
        "Summary:\nFeature: Y"
    )
    assert core._is_projected_feedback(projected) is True
    assert core.project_skill_assistant_content_for_input_items(projected, session_id="s", round_num=1) == projected


def test_assistant_skill_marker_without_ctx_ref_still_truncates():
    from src.agents import core

    malicious = "[assistant skill content compacted | ref=none]\n" + ("x" * 50000)
    output = core._tool_feedback_text_for_tool("github_get_pull_request", malicious)
    assert "chars hidden" in output
    assert len(output) < len(malicious)


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
    assert assistant_item["content"].count("[assistant skill content compacted") == 1
    assert tool_output_item["output"] != long_jira
    assert "ctx://context/" in assistant_item["content"]
    assert "context_ref: ctx://context/" in tool_output_item["output"]
    assert "Acceptance Criteria" in tool_output_item["output"]
    jira_ref = re.search(r"context_ref:\s*(ctx://context/[^\s]+)", tool_output_item["output"]).group(1)
    assert read_ref(jira_ref, session_id="s-mobilex", max_chars=30000) == long_jira


def test_build_responses_input_items_keeps_already_projected_assistant_tool_call_content():
    from src.agents import core

    projected = (
        "[assistant tool-call content compacted | original_chars=20000 | ref=ctx://context/s/assistant_output/aaaaaaaaaaaa]\n"
        "Summary:\nFeature: X\nScenario: Y"
    )
    messages = [
        {
            "role": "assistant",
            "content": projected,
            "tool_calls": [{"id": "call_proj", "function": {"name": "jira_get_issue", "arguments": "{}"}}],
        }
    ]
    items = core.build_responses_input_items(messages, session_id="s")
    assistant_item = next(item for item in items if item.get("role") == "assistant")
    function_call = next(item for item in items if item.get("type") == "function_call")
    assert assistant_item["content"] == projected
    assert function_call["call_id"] == "call_proj"
    assert "[assistant skill content compacted" not in assistant_item["content"]


def test_build_responses_input_items_projects_plain_assistant_large_gherkin():
    from src.agents import core

    long_gherkin = "@MMGFX-13887\nFeature: OCO TP\n" + ("\nScenario: L\nGiven a\nWhen b\nThen c" * 3000)
    messages = [
        {"role": "assistant", "content": long_gherkin},
        {"role": "user", "content": "continue"},
    ]
    items = core.build_responses_input_items(messages, session_id="s-plain")
    assistant_item = next(item for item in items if item.get("role") == "assistant")
    assert assistant_item["content"] != long_gherkin
    assert "ctx://context/" in assistant_item["content"]
    assert "[assistant skill content compacted" in assistant_item["content"]
    assert long_gherkin[:200] not in assistant_item["content"]


def test_build_responses_input_items_keeps_already_projected_plain_assistant_unchanged():
    from src.agents import core

    projected = (
        "[assistant output compacted | original_chars=20000 | ref=ctx://context/s/assistant_output/aaaaaaaaaaaa]\n"
        "Summary:\nFeature: X"
    )
    messages = [{"role": "assistant", "content": projected}]
    items = core.build_responses_input_items(messages, session_id="s")
    assistant_item = next(item for item in items if item.get("role") == "assistant")
    assert assistant_item["content"] == projected


def test_mobilex_large_generation_output_guard_is_unconditional_in_tool_loop_and_skill_mode():
    from src.agents import core

    process_source = inspect.getsource(core.Agent.process)
    continue_source = inspect.getsource(core.Agent._continue_skill_mode)
    assert "_large_generation_output_guard(" in process_source
    assert "_large_generation_output_guard(" in continue_source
    assert "Large generation output guard:" in inspect.getsource(core._large_generation_output_guard)


def test_large_generation_output_guard_applies_to_non_mobilex_generation_skill():
    from src.agents import core

    class Skill:
        name = "api-test-generator"
        description = "Generate integration tests and implementation files"

    text = core._large_generation_output_guard(None, Skill(), {"skill_name": "api-test-generator"}, "generate all test cases from this Jira")
    assert "Large generation output guard:" in text
    assert "generation_mode=staged" in text


def test_large_generation_output_guard_not_applied_to_normal_chat():
    from src.agents import core

    class Skill:
        name = "chat-helper"
        description = "General discussion assistant"

    text = core._large_generation_output_guard(None, Skill(), {"skill_name": "chat-helper"}, "what is jira?")
    assert text == ""


def test_requires_source_complete_context_detects_full_source_generation_intent():
    from src.agents import core

    class Skill:
        name = "api-test-generator"
        description = "generate tests from jira"

    assert core._requires_source_complete_context(
        "please use all Jira information and generate all test cases from https://x/browse/ABC-1",
        None,
        Skill(),
        [],
    ) is True


def test_agent_process_source_auto_invokes_source_prepare_and_max_output_recovery_paths_present():
    from src.agents import core

    process_source = inspect.getsource(core.Agent.process)
    assert "_requires_source_complete_context(" in process_source
    assert "jira_prepare_issue_context" in process_source
    assert "confluence_prepare_page_context" in process_source
    assert "call_llm_with_output_control(" in process_source
    assert "[auto source context prepared]" in process_source
    assert "output_controller_applied" in process_source


@pytest.mark.asyncio
async def test_recover_max_output_tokens_success_path(monkeypatch):
    from src.agents import core

    async def _fake_responses(**kwargs):
        return {"content": "manifest", "tool_calls": [], "function_calls": [], "usage": {}}

    monkeypatch.setattr(core.llm_client, "responses", _fake_responses)
    recovered, did_recover = await core._recover_max_output_tokens(
        llm_result={"error": {"code": "max_output_tokens_exceeded", "message": "x"}},
        llm_kwargs={"input_items": [], "system_prompt": "", "tools": []},
        loop_context_state={"budget": {}},
        stage_hint="tool_loop",
    )
    assert did_recover is True
    assert recovered.get("content") == "manifest"
    assert "error" not in recovered


@pytest.mark.asyncio
async def test_recover_max_output_tokens_fallback_path(monkeypatch):
    from src.agents import core

    async def _fake_responses(**kwargs):
        return {"error": {"code": "max_output_tokens_exceeded", "message": "x"}}

    monkeypatch.setattr(core.llm_client, "responses", _fake_responses)
    recovered, did_recover = await core._recover_max_output_tokens(
        llm_result={"error": {"code": "max_output_tokens_exceeded", "message": "x"}},
        llm_kwargs={"input_items": [], "system_prompt": "", "tools": []},
        loop_context_state={"budget": {}},
        stage_hint="tool_loop",
    )
    assert did_recover is True
    assert "error" not in recovered
    assert "staged generation" in recovered.get("content", "")


@pytest.mark.asyncio
async def test_output_controller_recovers_max_output_without_raw_fatal():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        def __init__(self):
            self.calls = 0
        async def responses(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"error": {"code": "max_output_tokens_exceeded", "message": "Model output was truncated because max_output_tokens was reached"}}
            return {"content": "manifest", "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate full implementation", "tools": []},
        session_id="s-out",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate implementation from jira",
    )
    assert "error" not in result
    assert "max_output_tokens was reached" not in (result.get("content") or "")
    assert diag["max_output_recovery"]["applied"] is True
    assert state["budget"]["generation_mode"] == "staged"
    assert state["budget"]["output_risk_level"] == "high"


@pytest.mark.asyncio
async def test_output_controller_bounds_huge_content_in_staged_mode():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "X" * 50000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate full implementation", "tools": []},
        session_id="s-out-big",
        stage="skill_generation",
        context_state=state,
        latest_user_text="generate all test cases",
        max_chat_output_chars=480000,
    )
    assert len(result.get("content") or "") == 50000
    assert "context_read_ref(" not in (result.get("content") or "")
    assert diag.get("output_bounding", {}).get("bounded") is False
    assert diag.get("generation", {}).get("generation_mode") == "staged"


@pytest.mark.asyncio
async def test_output_controller_allows_20k_high_risk_without_oversize_manifest():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "H" * 20000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate implementation", "tools": [], "model": "gpt-5.4-mini"},
        session_id="s-20k",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate full implementation",
        max_chat_output_chars=None,
    )
    assert len(result.get("content") or "") == 20000
    assert "saved the oversized draft" not in (result.get("content") or "").lower()
    assert diag.get("output_bounding", {}).get("bounded") is False


def test_resolve_output_boundary_uses_model_limit_tokens(monkeypatch):
    from src.config import config, resolve_output_boundary

    monkeypatch.setitem(config._config, "llm", {"model": "gpt-5.4-mini", "max_tokens": 128000})
    boundary = resolve_output_boundary("gpt-5.4-mini")
    assert boundary["max_output_tokens"] == 128000
    assert boundary["max_chat_output_tokens"] == 120000
    assert boundary["max_chat_output_chars"] == 120000 * 4


@pytest.mark.parametrize(
    ("model", "expected_output", "expected_chat_tokens", "expected_chars"),
    [
        ("gpt-4o", 16384, 15360, 61440),
        ("gpt-4.1", 16384, 15360, 61440),
        # 60000 chat-token defaults are valid for 64k-output models (not for gpt-5.4-mini).
        ("gpt-5-mini", 64000, 60000, 240000),
        ("gpt-5.3-codex", 128000, 120000, 480000),
        ("gpt-5.4-mini", 128000, 120000, 480000),
        ("gemini-2.5-pro", 64000, 60000, 240000),
    ],
)
def test_resolve_output_boundary_for_authoritative_models(monkeypatch, model, expected_output, expected_chat_tokens, expected_chars):
    from src.config import config, resolve_output_boundary

    monkeypatch.setitem(config._config, "llm", {"model": model, "max_tokens": expected_output})
    boundary = resolve_output_boundary(model)
    assert boundary["max_output_tokens"] == expected_output
    assert boundary["max_chat_output_tokens"] == expected_chat_tokens
    assert boundary["max_chat_output_chars"] == expected_chars


def test_resolve_output_boundary_ignores_legacy_8k_override(monkeypatch):
    from src.config import config, resolve_output_boundary

    monkeypatch.setitem(
        config._config,
        "llm",
        {
            "model": "gpt-5.4-mini",
            "max_tokens": 128000,
            "output_controller": {"max_chat_output_chars": 7000, "chars_per_token_estimate": 4},
            "model_limits": {
                "gpt-5.4-mini": {
                    "max_context_window_tokens": 400000,
                    "max_prompt_tokens": 272000,
                    "max_output_tokens": 128000,
                }
            },
        },
    )
    boundary = resolve_output_boundary("gpt-5.4-mini")
    assert boundary["max_chat_output_chars"] == 120000 * 4
    assert boundary["legacy_max_chat_output_chars_ignored"] is True
    assert boundary["output_boundary_source"] == "model_limits_legacy_override_ignored"


def test_resolve_output_boundary_allows_low_override_when_explicit(monkeypatch):
    from src.config import config, resolve_output_boundary

    monkeypatch.setitem(
        config._config,
        "llm",
        {
                "model": "gpt-5.4-mini",
                "max_tokens": 128000,
                "output_controller": {
                "max_chat_output_chars": 7000,
                "allow_low_max_chat_output_chars": True,
                "chars_per_token_estimate": 4,
            },
            "model_limits": {
                "gpt-5.4-mini": {
                    "max_context_window_tokens": 400000,
                    "max_prompt_tokens": 272000,
                    "max_output_tokens": 128000,
                }
            },
        },
    )
    boundary = resolve_output_boundary("gpt-5.4-mini")
    assert boundary["max_chat_output_chars"] == 7000
    assert boundary["legacy_max_chat_output_chars_ignored"] is False


def test_resolve_output_boundary_defaults_to_derived_chars(monkeypatch):
    from src.config import config, resolve_output_boundary

    monkeypatch.setitem(
        config._config,
        "llm",
        {
            "model": "gpt-5.4-mini",
            "max_tokens": 128000,
            "output_controller": {"max_chat_output_tokens": 120000, "chars_per_token_estimate": 4},
            "model_limits": {
                "gpt-5.4-mini": {
                    "max_context_window_tokens": 400000,
                    "max_prompt_tokens": 272000,
                    "max_output_tokens": 128000,
                }
            },
        },
    )
    boundary = resolve_output_boundary("gpt-5.4-mini")
    assert boundary["max_chat_output_chars"] == 120000 * 4


def test_compaction_context_window_defaults_do_not_assume_8k():
    from src.agents import compaction

    assert inspect.signature(compaction.summarize_with_fallback).parameters["context_window"].default is None
    assert inspect.signature(compaction.summarize_in_stages).parameters["context_window"].default is None
    assert inspect.signature(compaction.compact_messages).parameters["context_window"].default is None


def test_output_controller_default_output_chars_uses_model_limits():
    from src.runtime.output_controller import _default_output_chars

    chars, source = _default_output_chars("gpt-5.4-mini")
    assert chars == 120000 * 4
    assert source != "emergency_fallback_8000"


@pytest.mark.asyncio
async def test_output_controller_treats_warning_truncation_as_recovery():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        def __init__(self):
            self.calls = 0
        async def responses(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "content": "partial body",
                    "truncated": True,
                    "warning": {"type": "truncated_response", "code": "max_output_tokens_exceeded"},
                }
            return {"content": "manifest", "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate full spec", "tools": []},
        session_id="s-warn",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate tests",
    )
    assert "error" not in result
    assert diag["max_output_recovery"]["applied"] is True
    assert state["budget"]["output_controller_recovery_reason"] == "max_output_tokens"
    assert "Model output was truncated because max_output_tokens" not in (result.get("content") or "")


@pytest.mark.asyncio
async def test_output_controller_generation_state_machine_advances_on_continue():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "phase content", "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    _, diag1 = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate implementation", "tools": []},
        session_id="s-gen",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate implementation",
    )
    assert diag1.get("generation", {}).get("current_generation_phase") == "manifest"
    assert diag1.get("generation", {}).get("output_controller_stage") == "tool_loop"
    assert diag1.get("generation", {}).get("completion_criteria_count", 0) >= 1
    _, diag2 = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "continue staged", "tools": []},
        session_id="s-gen",
        stage="tool_loop",
        context_state=state,
        latest_user_text="continue",
    )
    assert diag2.get("generation", {}).get("current_generation_phase") in {"phase_1", "phase_2"}
    assert "source_digest_chunk_coverage_count" in diag2.get("generation", {})


@pytest.mark.asyncio
async def test_output_controller_generation_done_requires_completion_criteria():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "X" * 50000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {
        "budget": {},
        "generation": {
            "completion_criteria": ["manifest_prepared", "phase_output_recorded"],
            "completion_criteria_status": {"manifest_prepared": False, "phase_output_recorded": False},
            "generated_artifact_ref_count": 2,
        },
    }
    _, diag1 = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate implementation", "tools": []},
        session_id="s-gen-criteria",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate implementation",
    )
    assert diag1.get("generation_done") is False
    state["generation"]["completion_criteria_status"] = {"manifest_prepared": True, "phase_output_recorded": True}
    _, diag2 = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "continue", "tools": []},
        session_id="s-gen-criteria",
        stage="tool_loop",
        context_state=state,
        latest_user_text="continue",
    )
    assert diag2.get("generation_done") is True


@pytest.mark.asyncio
async def test_output_controller_tracks_generated_artifacts_by_phase_when_bounded():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "X" * 500000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    _, diag1 = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate implementation", "tools": []},
        session_id="s-gen-phase",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate implementation",
    )
    by_phase = diag1.get("generation", {}).get("generated_artifacts_by_phase", {})
    assert isinstance(by_phase, dict)
    assert "manifest" in by_phase
    assert len(by_phase["manifest"]) == 1


@pytest.mark.asyncio
async def test_output_controller_allows_20k_below_real_boundary():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "M" * 20000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "chat", "tools": [], "model": "gpt-5.4-mini"},
        session_id="s-medium-20k",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate docs",
        max_chat_output_chars=None,
    )
    assert len(result.get("content") or "") == 20000
    assert diag.get("output_bounding", {}).get("bounded") is False


@pytest.mark.asyncio
async def test_output_controller_bounds_huge_content_medium_risk():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "M" * 50000, "tool_calls": [], "function_calls": [], "usage": {}}

    user_text = "x" * 1500
    state = {"budget": {}}
    result, _ = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "chat", "tools": []},
        session_id="s-medium",
        stage="tool_loop",
        context_state=state,
        latest_user_text=user_text,
        max_chat_output_chars=480000,
    )
    assert len(result.get("content") or "") == 50000


@pytest.mark.asyncio
async def test_output_controller_allows_100k_below_real_boundary():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "M" * 100000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "chat", "tools": [], "model": "gpt-5.4-mini"},
        session_id="s-medium-100k",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate docs",
        max_chat_output_chars=None,
    )
    assert len(result.get("content") or "") == 100000
    assert diag.get("output_bounding", {}).get("bounded") is False


@pytest.mark.asyncio
async def test_output_controller_injects_effective_max_tokens_when_missing(monkeypatch):
    from src.config import config
    from src.runtime.output_controller import call_llm_with_output_control

    monkeypatch.setitem(
        config._config,
        "llm",
        {"model": "gpt-5.4-mini", "max_tokens": 64000, "allow_lower_max_tokens_than_model_limit": False},
    )
    captured = {}

    class _Client:
        async def responses(self, **kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return {"content": "ok", "tool_calls": [], "function_calls": [], "usage": {}}

    _, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "chat", "tools": [], "model": "gpt-5.4-mini"},
        session_id="s-max-missing",
        stage="tool_loop",
        context_state={"budget": {}},
        latest_user_text="hi",
        max_chat_output_chars=None,
    )
    assert captured["max_tokens"] == 128000
    assert diag.get("effective_max_tokens") == 128000
    assert diag.get("legacy_max_tokens_ignored") is True


@pytest.mark.asyncio
async def test_output_controller_ignores_stale_low_caller_max_tokens_when_not_allowed(monkeypatch):
    from src.config import config
    from src.runtime.output_controller import call_llm_with_output_control

    monkeypatch.setitem(
        config._config,
        "llm",
        {"model": "gpt-5.4-mini", "max_tokens": 64000, "allow_lower_max_tokens_than_model_limit": False},
    )
    captured = {}

    class _Client:
        async def responses(self, **kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return {"content": "ok", "tool_calls": [], "function_calls": [], "usage": {}}

    _, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "chat", "tools": [], "model": "gpt-5.4-mini", "max_tokens": 64000},
        session_id="s-max-low",
        stage="tool_loop",
        context_state={"budget": {}},
        latest_user_text="hi",
        max_chat_output_chars=None,
    )
    assert captured["max_tokens"] == 128000
    assert diag.get("caller_max_tokens_ignored") is True


@pytest.mark.asyncio
async def test_output_controller_honors_explicit_allow_lower_max_tokens(monkeypatch):
    from src.config import config
    from src.runtime.output_controller import call_llm_with_output_control

    monkeypatch.setitem(
        config._config,
        "llm",
        {"model": "gpt-5.4-mini", "max_tokens": 64000, "allow_lower_max_tokens_than_model_limit": True},
    )
    captured = {}

    class _Client:
        async def responses(self, **kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return {"content": "ok", "tool_calls": [], "function_calls": [], "usage": {}}

    _, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "chat", "tools": [], "model": "gpt-5.4-mini", "max_tokens": 64000},
        session_id="s-max-allow-low",
        stage="tool_loop",
        context_state={"budget": {}},
        latest_user_text="hi",
        max_chat_output_chars=None,
    )
    assert captured["max_tokens"] == 64000
    assert diag.get("caller_max_tokens_ignored") is False


@pytest.mark.asyncio
async def test_output_controller_ignores_stale_budget_max_chat_output_chars(monkeypatch):
    from src.config import config
    from src.runtime.output_controller import call_llm_with_output_control

    monkeypatch.setitem(
        config._config,
        "llm",
        {
            "model": "gpt-5.4-mini",
            "max_tokens": 128000,
            "allow_lower_max_tokens_than_model_limit": False,
            "output_controller": {"chars_per_token_estimate": 4},
        },
    )

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "M" * 50000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {"max_chat_output_chars": 8000}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "chat", "tools": [], "model": "gpt-5.4-mini"},
        session_id="s-stale-budget",
        stage="tool_loop",
        context_state=state,
        latest_user_text="normal response",
        max_chat_output_chars=None,
    )
    assert len(result.get("content") or "") == 50000
    assert diag.get("output_bounding", {}).get("bounded") is False
    assert diag.get("max_chat_output_chars") == 480000
    assert diag.get("budget_max_chat_output_chars_ignored") is True
    assert diag.get("configured_budget_max_chat_output_chars") == "8000"


@pytest.mark.asyncio
async def test_output_controller_ignores_stale_arg_max_chat_output_chars(monkeypatch):
    from src.config import config
    from src.runtime.output_controller import call_llm_with_output_control

    monkeypatch.setitem(
        config._config,
        "llm",
        {
            "model": "gpt-5.4-mini",
            "max_tokens": 128000,
            "output_controller": {"chars_per_token_estimate": 4},
        },
    )

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "M" * 50000, "tool_calls": [], "function_calls": [], "usage": {}}

    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "chat", "tools": [], "model": "gpt-5.4-mini"},
        session_id="s-stale-arg",
        stage="tool_loop",
        context_state={"budget": {}},
        latest_user_text="normal response",
        max_chat_output_chars=8000,
    )
    assert len(result.get("content") or "") == 50000
    assert diag.get("output_bounding", {}).get("bounded") is False
    assert diag.get("max_chat_output_chars") == 480000
    assert diag.get("arg_max_chat_output_chars_ignored") is True
    assert diag.get("configured_arg_max_chat_output_chars") == "8000"
    assert diag.get("output_boundary_source") != "emergency_fallback_8000"


@pytest.mark.asyncio
async def test_output_controller_allows_low_budget_max_chat_output_chars_when_explicit(monkeypatch):
    from src.config import config
    from src.runtime.output_controller import call_llm_with_output_control

    monkeypatch.setitem(
        config._config,
        "llm",
        {
            "model": "gpt-5.4-mini",
            "max_tokens": 128000,
            "output_controller": {"allow_low_max_chat_output_chars": True, "chars_per_token_estimate": 4},
        },
    )

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "M" * 20000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {"max_chat_output_chars": 8000}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "chat", "tools": [], "model": "gpt-5.4-mini"},
        session_id="s-allow-low-budget",
        stage="tool_loop",
        context_state=state,
        latest_user_text="normal response",
        max_chat_output_chars=None,
    )
    assert len(result.get("content") or "") < 1000
    assert diag.get("output_bounding", {}).get("bounded") is True
    assert diag.get("max_chat_output_chars") == 8000
    assert diag.get("budget_max_chat_output_chars_ignored") is False


@pytest.mark.asyncio
async def test_output_controller_retry_max_output_still_returns_non_error_fallback():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"error": {"code": "max_output_tokens_exceeded", "message": "Model output was truncated because max_output_tokens was reached"}}

    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate complete implementation", "tools": []},
        session_id="s-retry-fallback",
        stage="tool_loop",
        context_state={"budget": {}},
        latest_user_text="generate all tests and code",
    )
    assert "error" not in result
    assert diag["max_output_recovery"]["applied"] is True
    assert "Model output was truncated because max_output_tokens" not in (result.get("content") or "")


@pytest.mark.asyncio
async def test_output_controller_accepts_none_max_chat_output_chars():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "N" * 9000, "tool_calls": [], "function_calls": [], "usage": {}}

    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "simple", "tools": [], "model": "gpt-5.4-mini"},
        session_id="s-none-max-chat",
        stage="tool_loop",
        context_state={"budget": {}},
        latest_user_text="Hey",
        max_chat_output_chars=None,
    )
    assert len(result.get("content") or "") == 9000
    assert diag.get("output_bounding", {}).get("bounded") is False
    assert diag.get("max_chat_output_chars") == 480000


def test_ensure_staged_generation_accepts_none_max_chat_output_chars():
    from src.runtime.output_controller import ensure_staged_generation

    state = {"generation": {}}
    gen = ensure_staged_generation(state, stage="tool_loop", max_chat_output_chars=None)
    assert gen.get("max_chat_output_chars") >= 120000 * 4


@pytest.mark.asyncio
async def test_output_controller_bounds_oversized_normal_risk_output():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "N" * 50000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "simple chat reply", "tools": []},
        session_id="s-normal",
        stage="tool_loop",
        context_state=state,
        latest_user_text="hello",
        max_chat_output_chars=480000,
    )
    assert len(result.get("content") or "") == 50000
    assert diag.get("output_bounding", {}).get("bounded") is False
    assert state["budget"].get("oversized_output_saved") is False


@pytest.mark.asyncio
async def test_output_controller_bounds_only_when_exceeding_real_boundary():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "N" * 500000, "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    result, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "simple chat reply", "tools": [], "model": "gpt-5.4-mini"},
        session_id="s-normal-oversized",
        stage="tool_loop",
        context_state=state,
        latest_user_text="hello",
        max_chat_output_chars=None,
    )
    assert len(result.get("content") or "") < 1000
    assert diag.get("output_bounding", {}).get("bounded") is True
    assert state["budget"].get("oversized_output_saved") is True
    assert "generated_artifact_ref_count=1" in (result.get("content") or "")
    assert "next_phase=phase_1" in (result.get("content") or "")


def test_core_and_skill_mode_do_not_call_llm_client_responses_directly():
    from src.agents import core
    from src.agents import skill_mode

    assert "llm_client.responses(" not in inspect.getsource(core.Agent.process)
    assert "llm_client.responses(" not in inspect.getsource(core._run_skill_finalizer)
    assert "llm_client.responses(" not in inspect.getsource(skill_mode._generate_initial_skill_plan_direct)


def test_find_source_target_for_context_can_reuse_previous_jira_and_confluence_messages():
    from src.agents import core

    jira = core._find_source_target_for_context(
        latest_user_text="基于刚才全部Jira信息生成测试",
        messages=[{"role": "user", "content": "https://x/browse/ABC-123"}],
        context_state={},
    )
    assert jira["source_type"] == "jira"
    conf = core._find_source_target_for_context(
        latest_user_text="use all confluence info",
        messages=[{"role": "user", "content": "https://wiki.local/pages/123/Title"}],
        context_state={},
    )
    assert conf["source_type"] == "confluence"


def test_auto_source_manifest_as_assistant_message_does_not_create_orphan_function_call_output():
    from src.agents import core

    messages = [
        {"role": "assistant", "content": "[auto source context prepared]\n[jira source bundle prepared]\ncontext_ref: ctx://context/s/k/aaaaaaaaaaaa"},
        {"role": "user", "content": "continue"},
    ]
    items = core.build_responses_input_items(messages, session_id="s")
    assert not any(item.get("type") == "function_call_output" for item in items)


def test_mobilex_skill_prompt_contains_hard_output_constraints():
    from pathlib import Path

    skill_text = Path("skills/mobilex-test-cases-generator/skill.md").read_text(encoding="utf-8")
    assert "## Hard Output Constraints" in skill_text
    assert "Never generate full multi-file Java implementation in one response." in skill_text
    assert "one file at a time" in skill_text


def test_mobilex_continuation_regression_shape_projects_plain_assistant_and_jira():
    from src.agents import core

    long_gherkin = "@MMGFX-13887\nFeature: OCO TP\n" + ("\nScenario: L\nGiven a\nWhen b\nThen c" * 3000)
    long_jira = (
        "# MMGFX-13887: OCO\n## Description\n"
        + ("noise\n" * 3500)
        + "## Acceptance Criteria\n- ac1\n- ac2\n"
    )
    messages = [
        {"role": "assistant", "content": long_gherkin},
        {"role": "user", "content": "continue"},
        {
            "role": "assistant",
            "content": "calling jira",
            "tool_calls": [{"id": "call_1", "function": {"name": "jira_get_issue", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "tool_name": "jira_get_issue", "content": long_jira},
    ]

    raw_items = [
        {"role": "assistant", "content": long_gherkin},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
        {"role": "assistant", "content": "calling jira"},
        {"type": "function_call", "call_id": "call_1", "name": "jira_get_issue", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": long_jira},
    ]
    raw_tokens = core.estimate_llm_request_tokens(raw_items, "", [])

    items = core.build_responses_input_items(messages, session_id="s-regress")
    new_tokens = core.estimate_llm_request_tokens(items, "", [])
    assistant_item = next(item for item in items if item.get("role") == "assistant" and isinstance(item.get("content"), str))
    tool_output_item = next(item for item in items if item.get("type") == "function_call_output")
    function_call = next(item for item in items if item.get("type") == "function_call")

    assert long_gherkin[:200] not in assistant_item["content"]
    assert "ctx://context/" in assistant_item["content"]
    assert "context_ref: ctx://context/" in tool_output_item["output"]
    assert "context_read_ref(" in tool_output_item["output"]
    assert long_jira[:500] not in tool_output_item["output"]
    assert new_tokens < raw_tokens
    assert tool_output_item["call_id"] == function_call["call_id"]


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


def test_degrade_projected_context_sources_in_responses_preserves_ref_and_read_instruction():
    from src.agents import core

    large_envelope = (
        "[large source tool result projected]\n"
        "tool_name: jira_get_issue\n"
        "kind: jira_issue\n"
        "context_ref: ctx://context/s/k/aaaaaaaaaaaa\n"
        "original_chars: 22000\n"
        "model_view_chars: 7000\n"
        "full_content_available: true\n"
        "section_map:\n- One\n- Two\n\n"
        "preview:\n" + ("X" * 8000) + "\n\n"
        "To read more, call: context_read_ref(ref=\"ctx://context/s/k/aaaaaaaaaaaa\", section=\"raw\", max_chars=6000)\n"
    )
    items = [{"type": "function_call_output", "call_id": "call_1", "output": large_envelope}]
    degraded = core.degrade_projected_context_sources_in_responses_input_items(items, max_envelope_chars=700)
    output = degraded[0]["output"]
    assert degraded[0]["call_id"] == "call_1"
    assert len(output) < len(large_envelope)
    assert "context_ref: ctx://context/" in output
    assert "original_chars:" in output
    assert "context_read_ref(" in output


def test_degrade_projected_context_sources_keeps_assistant_skill_ref():
    from src.agents import core

    projected = (
        "[assistant skill content compacted | original_chars=21000 | ref=ctx://context/s/k/bbbbbbbbbbbb]\n"
        "Summary:\nFeature: Y\n" + ("extra\n" * 500)
    )
    items = [{"type": "function_call_output", "call_id": "call_2", "output": projected}]
    degraded = core.degrade_projected_context_sources_in_responses_input_items(items, max_envelope_chars=300)
    assert degraded[0]["call_id"] == "call_2"
    assert "ref=ctx://context/" in degraded[0]["output"]


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


def test_is_tool_allowed_by_skill_runtime_allows_internal_support_tools():
    from src.agents import core
    from src.skills.runtime import SkillRuntimeConfig

    runtime_cfg = SkillRuntimeConfig(
        skill_name="demo",
        allowed_tools=["jira_get_issue"],
        allowed_tools_set={"jira_get_issue"},
        tool_policy_declared=True,
    )
    assert core._is_tool_allowed_by_skill_runtime("jira_get_issue", runtime_cfg) is True
    assert core._is_tool_allowed_by_skill_runtime("context_read_ref", runtime_cfg) is True
    assert core._is_tool_allowed_by_skill_runtime("github_push", runtime_cfg) is False


def test_skill_schema_list_branch_keeps_context_read_ref_if_globally_available():
    from src.agents import core

    self_tools = [
        {"type": "function", "function": {"name": "context_read_ref"}},
        {"type": "function", "function": {"name": "jira_get_issue_by_url"}},
    ]
    skill_tool_schemas = [{"type": "function", "function": {"name": "jira_get_issue_by_url"}}]
    globally_allowed_tool_names = {"jira_get_issue_by_url", "context_read_ref"}

    available_tools = [
        schema for schema in skill_tool_schemas
        if (core.extract_tool_name(schema) or "") in globally_allowed_tool_names
    ]
    support_tool_schemas = [
        schema
        for schema in self_tools
        if (core.extract_tool_name(schema) or "").lower() in {name.lower() for name in core.INTERNAL_SUPPORT_TOOL_NAMES}
    ]
    existing_tool_names = {(core.extract_tool_name(schema) or "").lower() for schema in available_tools}
    for schema in support_tool_schemas:
        support_name = (core.extract_tool_name(schema) or "").lower()
        if support_name and support_name not in existing_tool_names:
            available_tools.append(schema)
            existing_tool_names.add(support_name)

    names = {core.extract_tool_name(schema) for schema in available_tools}
    assert "jira_get_issue_by_url" in names
    assert "context_read_ref" in names


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
        "request_budget_stage",
        "stage",
        "suggestion",
    ):
        assert key in details
    assert details["request_budget_stage"] == "tool_loop"
    for unsafe_key in ("prompt", "payload", "input_items", "tools", "source_docs", "raw_model_response", "api_key"):
        assert unsafe_key not in details


def test_merge_request_budget_into_context_state_merges_safe_fields():
    from src.agents import core

    merged = core._merge_request_budget_into_context_state(
        {"budget": {"existing": 1}},
        {
            "request_estimated_tokens": 123,
            "request_over_budget": True,
            "stage": "skill_finalizer",
            "large_generation_guard_applied": True,
            "output_size_guard_applied": True,
            "ignored_key": "x",
        },
    )
    assert merged["budget"]["existing"] == 1
    assert merged["budget"]["request_estimated_tokens"] == 123
    assert merged["budget"]["request_over_budget"] is True
    assert merged["budget"]["large_generation_guard_applied"] is True
    assert merged["budget"]["output_size_guard_applied"] is True
    assert merged["budget"]["request_budget_stage"] == "skill_finalizer"
    assert "ignored_key" not in merged["budget"]


def test_safe_request_budget_fields_includes_guard_flags():
    from src.agents import core

    safe = core._safe_request_budget_fields(
        {
            "request_estimated_tokens": 10,
            "large_generation_guard_applied": True,
            "output_size_guard_applied": True,
            "prompt": "do-not-include",
        }
    )
    assert safe["request_estimated_tokens"] == 10
    assert safe["large_generation_guard_applied"] is True
    assert safe["output_size_guard_applied"] is True
    assert "prompt" not in safe


def test_agent_process_source_attaches_runtime_events_for_early_budget_and_llm_errors():
    from src.agents import core

    source = inspect.getsource(core.Agent.process)
    assert "return attach_runtime_events(error_response)" in source
    assert "_merge_budget_into_error_details(error_response, latest_request_budget)" in source


def test_continue_skill_mode_source_merges_budget_into_llm_errors():
    from src.agents import core

    source = inspect.getsource(core.Agent._continue_skill_mode)
    assert "_merge_budget_into_error_details(error_response, skill_request_budget)" in source


def test_agent_process_source_records_large_generation_guard_budget_fields():
    from src.agents import core

    source = inspect.getsource(core.Agent.process)
    assert 'budget_state["large_generation_guard_applied"] = large_generation_guard_applied' in source
    assert 'budget_state["output_size_guard_applied"] = large_generation_guard_applied' in source


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
