import pytest


@pytest.mark.asyncio
async def test_legacy_jira_tools_are_not_runtime_root_dispatch_tools():
    from src import execute_tool, get_tool_names

    assert "jira_get_issue" not in get_tool_names()

    result = await execute_tool(
        "jira_get_issue",
        issue_key="EFP-123",
        format=None,
        max_comments=None,
        include_comments=None,
        include_attachment_urls=None,
    )

    assert result.success is False
