import pytest

from src.runtime.tool_filtering import (
    filter_tool_schemas_for_llm,
    is_tool_name_enabled_for_llm,
    normalize_llm_tools_spec,
)


TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "git_clone", "description": "clone"}},
    {"type": "function", "function": {"name": "jira_get_issue", "description": "jira"}},
    {"type": "function", "function": {"name": "jira_search", "description": "jira"}},
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


@pytest.mark.parametrize("tools_value", ["*", ["*"]])
def test_normalize_wildcard_all(tools_value):
    spec = normalize_llm_tools_spec({"tools": tools_value})
    assert spec.mode == "all"


def test_filter_single_pattern_jira_prefix():
    result = filter_tool_schemas_for_llm(TOOL_SCHEMAS, {"tools": "jira_*"})
    assert _names(result) == ["jira_get_issue", "jira_search"]


def test_filter_union_exact_and_wildcard():
    result = filter_tool_schemas_for_llm(TOOL_SCHEMAS, {"tools": ["git_clone", "jira_*"]})
    assert _names(result) == ["git_clone", "jira_get_issue", "jira_search"]


def test_filter_case_insensitive_pattern():
    result = filter_tool_schemas_for_llm(TOOL_SCHEMAS, {"tools": ["JIRA_*", "Git_Clone"]})
    assert _names(result) == ["git_clone", "jira_get_issue", "jira_search"]


def test_filter_unmatched_patterns_reported():
    result = filter_tool_schemas_for_llm(TOOL_SCHEMAS, {"tools": ["jira_*", "unknown_*"]})
    assert result.unmatched_patterns == ["unknown_*"]


def test_normalize_list_non_string_raises():
    with pytest.raises(ValueError):
        normalize_llm_tools_spec({"tools": ["jira_*", 1]})


def test_is_tool_name_enabled_matches_filtering_rules():
    llm_config = {"tools": ["git_clone", "jira_*"]}
    assert is_tool_name_enabled_for_llm("git_clone", llm_config) is True
    assert is_tool_name_enabled_for_llm("JIRA_SEARCH", llm_config) is True
    assert is_tool_name_enabled_for_llm("read", llm_config) is False
