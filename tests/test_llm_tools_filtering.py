import pytest

from src.runtime.tool_filtering import (
    filter_tool_schemas_for_llm,
    is_internal_support_tool_name,
    is_tool_name_enabled_for_llm,
    normalize_llm_tools_spec,
)


TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "git_clone", "description": "clone"}},
    {"type": "function", "function": {"name": "jira_get_issue", "description": "jira"}},
    {"type": "function", "function": {"name": "jira_search", "description": "jira"}},
    {"type": "function", "function": {"name": "context_read_ref", "description": "internal"}},
    {"type": "function", "function": {"name": "read", "description": "read"}},
]


def _names(result):
    return [item["function"]["name"] for item in result.filtered_schemas]


def test_normalize_missing_tools_is_all():
    spec = normalize_llm_tools_spec({"model": "x"})
    assert spec.configured is False
    assert spec.mode == "all"


@pytest.mark.parametrize(
    "tools_value",
    [[], None, "", "   "],
)
def test_normalize_explicit_empty_is_none(tools_value):
    spec = normalize_llm_tools_spec({"tools": tools_value})
    assert spec.configured is True
    assert spec.mode == "none"

def test_missing_llm_tools_defaults_to_all():
    spec = normalize_llm_tools_spec({})
    assert spec.configured is False
    assert spec.mode == "all"


def test_explicit_wildcard_tools_means_all():
    spec = normalize_llm_tools_spec({"tools": ["*"]})
    assert spec.configured is True
    assert spec.mode == "all"


def test_explicit_empty_tools_means_none():
    spec = normalize_llm_tools_spec({"tools": []})
    assert spec.configured is True
    assert spec.mode == "none"


def test_filter_tools_defaults_and_explicit_modes_with_regular_tools_only():
    regular_tools = [
        {"type": "function", "function": {"name": "git_clone", "description": "clone"}},
        {"type": "function", "function": {"name": "jira_get_issue", "description": "jira"}},
    ]

    missing_result = filter_tool_schemas_for_llm(regular_tools, {})
    wildcard_result = filter_tool_schemas_for_llm(regular_tools, {"tools": ["*"]})
    none_result = filter_tool_schemas_for_llm(regular_tools, {"tools": []})

    assert _names(missing_result) == ["git_clone", "jira_get_issue"]
    assert _names(wildcard_result) == ["git_clone", "jira_get_issue"]
    assert _names(none_result) == []



@pytest.mark.parametrize("tools_value", ["*", ["*"]])
def test_normalize_wildcard_all(tools_value):
    spec = normalize_llm_tools_spec({"tools": tools_value})
    assert spec.mode == "all"


def test_filter_single_pattern_jira_prefix():
    result = filter_tool_schemas_for_llm(TOOL_SCHEMAS, {"tools": "jira_*"})
    assert _names(result) == ["jira_get_issue", "jira_search", "context_read_ref"]


def test_filter_union_exact_and_wildcard():
    result = filter_tool_schemas_for_llm(TOOL_SCHEMAS, {"tools": ["git_clone", "jira_*"]})
    assert _names(result) == ["git_clone", "jira_get_issue", "jira_search", "context_read_ref"]


def test_filter_case_insensitive_pattern():
    result = filter_tool_schemas_for_llm(TOOL_SCHEMAS, {"tools": ["JIRA_*", "Git_Clone"]})
    assert _names(result) == ["git_clone", "jira_get_issue", "jira_search", "context_read_ref"]


def test_filter_unmatched_patterns_reported():
    result = filter_tool_schemas_for_llm(TOOL_SCHEMAS, {"tools": ["jira_*", "unknown_*"]})
    assert result.unmatched_patterns == ["unknown_*"]


def test_filter_explicit_none_does_not_include_internal_support_tools():
    result = filter_tool_schemas_for_llm(TOOL_SCHEMAS, {"tools": []})
    assert _names(result) == []


def test_is_tool_name_enabled_allows_internal_support_tool_in_pattern_mode():
    assert is_tool_name_enabled_for_llm("context_read_ref", {"tools": "jira_*"}) is True
    assert is_tool_name_enabled_for_llm("Context_Read_Ref", {"tools": "jira_*"}) is True
    assert is_tool_name_enabled_for_llm("context_read_ref", {"tools": []}) is False
    assert is_tool_name_enabled_for_llm("read", {"tools": "jira_*"}) is False


def test_is_internal_support_tool_name_case_insensitive():
    assert is_internal_support_tool_name("context_read_ref") is True
    assert is_internal_support_tool_name("Context_Read_Ref") is True
    assert is_internal_support_tool_name("jira_get_issue") is False


@pytest.mark.asyncio
async def test_execute_tool_by_name_allows_context_read_ref_under_pattern_mode(monkeypatch):
    from src.agents import executor as executor_module

    class _Cfg:
        llm = {"tools": ["jira_*"]}

    async def _fake_execute_tool(name, **kwargs):
        assert name == "context_read_ref"
        return executor_module.ToolResult(success=True, content="ok")

    monkeypatch.setattr(executor_module, "config", _Cfg())
    monkeypatch.setattr(executor_module, "execute_tool", _fake_execute_tool)

    result = await executor_module.execute_tool_by_name("context_read_ref", ref="ctx://context/s/k/aaaaaaaaaaaa")
    assert result.success is True


def test_normalize_list_non_string_raises():
    with pytest.raises(ValueError):
        normalize_llm_tools_spec({"tools": ["jira_*", 1]})


def test_is_tool_name_enabled_matches_filtering_rules():
    llm_config = {"tools": ["git_clone", "jira_*"]}
    assert is_tool_name_enabled_for_llm("git_clone", llm_config) is True
    assert is_tool_name_enabled_for_llm("JIRA_SEARCH", llm_config) is True
    assert is_tool_name_enabled_for_llm("read", llm_config) is False
