import inspect

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


@pytest.mark.parametrize(
    "tool_name",
    [
        "jira_get_issue",
        "jira_get_issue_by_url",
        "jira_search",
        "confluence_get_page",
        "confluence_get_page_by_url",
        "confluence_get_comments",
    ],
)
def test_tool_feedback_text_for_jira_and_confluence_is_unbounded(tool_name):
    from src.agents import core

    long_value = "A" * 20000
    output = core._tool_feedback_text_for_tool(tool_name, long_value)

    assert output == long_value
    assert "chars hidden" not in output


def test_tool_feedback_text_for_non_jira_confluence_uses_default_8000_limit():
    from src.agents import core

    long_value = "A" * 9005
    output = core._tool_feedback_text_for_tool("github_get_pull_request", long_value)

    assert output.startswith("A" * 8000)
    assert "1005 chars hidden" in output
    assert len(output) < len(long_value)


def test_unbounded_tool_feedback_prefix_policy_is_prefix_based():
    from src.agents import core

    assert core._is_unbounded_tool_feedback("jira_get_issue") is True
    assert core._is_unbounded_tool_feedback("jira_future_bundle") is True
    assert core._is_unbounded_tool_feedback("confluence_get_page") is True
    assert core._is_unbounded_tool_feedback("confluence_future_tool") is True
    assert core._is_unbounded_tool_feedback("github_get_pull_request") is False
    assert core._is_unbounded_tool_feedback("") is False
    assert core._is_unbounded_tool_feedback(None) is False


def test_tool_feedback_text_allows_explicit_unbounded_max_length():
    from src.agents import core

    value = "A" * 20000
    assert core._tool_feedback_text(value, max_length=None) == value
    assert core._tool_feedback_text(value, max_length=0) == value


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
