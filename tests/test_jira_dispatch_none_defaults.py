import pytest


@pytest.mark.asyncio
async def test_jira_get_issue_none_values_use_defaults(monkeypatch):
    from src import execute_tool

    captured = {}

    async def fake_jira_get_issue(issue_key, **kwargs):
        captured["issue_key"] = issue_key
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr("src.jira.jira_get_issue", fake_jira_get_issue)

    result = await execute_tool(
        "jira_get_issue",
        issue_key="EFP-123",
        format=None,
        max_comments=None,
        include_comments=None,
        include_attachment_urls=None,
    )

    assert result.success is True
    assert captured["issue_key"] == "EFP-123"
    assert captured["format"] == "markdown"
    assert captured["max_comments"] == 5
    assert captured["include_comments"] is True
    assert captured["include_attachment_urls"] is False


@pytest.mark.asyncio
async def test_jira_get_issue_preserves_false_and_zero(monkeypatch):
    from src import execute_tool

    captured = {}

    async def fake_jira_get_issue(issue_key, **kwargs):
        captured["issue_key"] = issue_key
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr("src.jira.jira_get_issue", fake_jira_get_issue)

    result = await execute_tool(
        "jira_get_issue",
        issue_key="EFP-456",
        include_comments=False,
        include_attachment_urls=False,
        max_comments=0,
        format="",
    )

    assert result.success is True
    assert captured["issue_key"] == "EFP-456"
    assert captured["include_comments"] is False
    assert captured["include_attachment_urls"] is False
    assert captured["max_comments"] == 0
    assert captured["format"] == ""
