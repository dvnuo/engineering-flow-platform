import pytest


def test_convert_tools_schema_preserves_optional_properties():
    from src.agents.llm import _convert_tools_schema

    tools = [{
        "type": "function",
        "function": {
            "name": "jira_get_issue",
            "description": "Get issue",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string"},
                    "max_chars": {"type": "integer"},
                    "include_comments": {"type": "boolean"},
                },
                "required": ["issue_key"],
            },
        },
    }]

    converted = _convert_tools_schema(tools)
    params = converted[0]["parameters"]
    assert params["required"] == ["issue_key"]
    assert "max_chars" not in params["required"]
    assert params["additionalProperties"] is False


@pytest.mark.parametrize(
    "user_message,expected",
    [
        ("Get Jira EFP-123 detail", True),
        ("Show issue EFP-123", True),
        ("Open this Jira URL and show me the issue", True),
        ("Summarize Jira EFP-123", False),
        ("Get issue EFP-123 and assign it to me", False),
        ("Show Jira EFP-123 and add a comment", False),
        ("Open issue EFP-123 and move it to In Progress", False),
        ("Read issue EFP-123 and update the assignee", False),
        ("Fetch issue EFP-123 then transition it", False),
        ("Retrieve Jira EFP-123 and close it", False),
    ],
)
def test_should_passthrough_tool_result_heuristic(user_message, expected):
    from src.agents.core import _should_passthrough_tool_result
    from src import ToolResult

    tool_calls = [{"name": "jira_get_issue", "call_id": "call_1", "arguments": {"issue_key": "EFP-123"}}]
    tool_result = ToolResult(success=True, content="# EFP-123: Details")

    actual = _should_passthrough_tool_result(
        latest_user_message=user_message,
        tool_calls=tool_calls,
        tool_name="jira_get_issue",
        tool_result=tool_result,
    )
    assert actual is expected
