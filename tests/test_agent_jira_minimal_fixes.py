import pytest


def test_convert_tools_schema_preserves_optional_semantics_with_nullable():
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
    assert params["required"] == ["issue_key", "max_chars", "include_comments"]
    assert params["properties"]["issue_key"]["type"] == "string"
    assert params["properties"]["max_chars"]["type"] == ["integer", "null"]
    assert params["properties"]["include_comments"]["type"] == ["boolean", "null"]
    assert params["additionalProperties"] is False


def test_convert_tools_schema_discover_commands_all_required_and_nullable():
    from src.agents.llm import _convert_tools_schema

    tools = [{
        "type": "function",
        "function": {
            "name": "discover_commands",
            "parameters": {
                "type": "object",
                "properties": {
                    "prefix": {"type": "string"},
                    "contains": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    }]

    params = _convert_tools_schema(tools)[0]["parameters"]
    assert params["required"] == ["prefix", "contains", "limit"]
    assert params["properties"]["prefix"]["type"] == ["string", "null"]
    assert params["properties"]["contains"]["type"] == ["string", "null"]
    assert params["properties"]["limit"]["type"] == ["integer", "null"]


def test_convert_tools_schema_run_command_keeps_cmd_non_nullable():
    from src.agents.llm import _convert_tools_schema

    tools = [{
        "type": "function",
        "function": {
            "name": "run_command",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "timeout_ms": {"type": "integer"},
                },
                "required": ["cmd"],
            },
        },
    }]

    params = _convert_tools_schema(tools)[0]["parameters"]
    assert params["required"] == ["cmd", "args", "cwd", "timeout_ms"]
    assert params["properties"]["cmd"]["type"] == "string"
    assert params["properties"]["args"]["type"] == ["array", "null"]
    assert params["properties"]["cwd"]["type"] == ["string", "null"]
    assert params["properties"]["timeout_ms"]["type"] == ["integer", "null"]


def test_convert_tools_schema_jira_get_issue_optional_fields_nullable():
    from src.agents.llm import _convert_tools_schema

    tools = [{
        "type": "function",
        "function": {
            "name": "jira_get_issue",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string"},
                    "format": {"type": "string", "enum": ["markdown", "wiki", "raw"]},
                    "max_chars": {"type": "integer"},
                    "max_comments": {"type": "integer"},
                    "include_fields": {"type": "array", "items": {"type": "string"}},
                    "include_comments": {"type": "boolean"},
                },
                "required": ["issue_key"],
            },
        },
    }]

    params = _convert_tools_schema(tools)[0]["parameters"]
    assert params["required"] == ["issue_key", "format", "max_chars", "max_comments", "include_fields", "include_comments"]
    assert params["properties"]["issue_key"]["type"] == "string"
    assert params["properties"]["format"]["type"] == ["string", "null"]
    assert params["properties"]["max_chars"]["type"] == ["integer", "null"]
    assert params["properties"]["max_comments"]["type"] == ["integer", "null"]
    assert params["properties"]["include_fields"]["type"] == ["array", "null"]
    assert params["properties"]["include_comments"]["type"] == ["boolean", "null"]


def test_convert_tools_schema_no_optional_properties_no_type_widening():
    from src.agents.llm import _convert_tools_schema

    tools = [{
        "type": "function",
        "function": {
            "name": "required_only",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["id", "name"],
            },
        },
    }]

    params = _convert_tools_schema(tools)[0]["parameters"]
    assert params["required"] == ["id", "name"]
    assert params["properties"]["id"]["type"] == "string"
    assert params["properties"]["name"]["type"] == "string"


@pytest.mark.parametrize(
    "user_message,expected",
    [
        ("Get Jira EFP-123 detail", True),
        ("Show issue EFP-123", True),
        ("Open this Jira URL and show me the issue", True),
        ("Summarize Jira EFP-123", False),
        ("Get issue EFP-123 and assign it to me", False),
        ("get issue EFP-123 and assign it to me", False),
        ("show issue then update assignee", False),
        ("Show Jira EFP-123 and add a comment", False),
        ("Open issue EFP-123 and move it to In Progress", False),
        ("open issue and move it to in progress", False),
        ("Read issue EFP-123 and update the assignee", False),
        ("read issue after that add comment", False),
        ("Fetch issue EFP-123 then transition it", False),
        ("Retrieve Jira EFP-123 and close it", False),
    ],
)
def test_should_passthrough_tool_result_heuristic(user_message, expected):
    from src.agents.tool_result_policy import should_passthrough_tool_result
    from src import ToolResult

    tool_result = ToolResult(success=True, content="# EFP-123: Details")

    actual = should_passthrough_tool_result(
        latest_user_message=user_message,
        tool_name="jira_get_issue",
        tool_result=tool_result,
        tool_calls_count=1,
    )
    assert actual is expected
