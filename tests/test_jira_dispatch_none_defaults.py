import pytest


@pytest.mark.asyncio
async def test_none_kwargs_are_stripped_before_dispatch(monkeypatch):
    from src import execute_tool

    captured = {}

    async def fake_github_search_issues(query, max_results):
        captured["query"] = query
        captured["max_results"] = max_results
        return "ok"

    monkeypatch.setattr("src.github.github_search_issues", fake_github_search_issues)

    result = await execute_tool(
        "github_search_issues",
        query="is:issue is:open label:bug",
        max_results=None,
    )

    assert result.success is True
    assert captured["query"] == "is:issue is:open label:bug"
    # If None is not stripped first, dispatch passes None instead of the default.
    assert captured["max_results"] == 10


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


@pytest.mark.asyncio
async def test_github_get_pr_dispatch_does_not_fail_on_error_word_in_body(monkeypatch):
    from src import execute_tool

    async def fake_github_get_pr(owner, repo, pull_number):
        return "**PR acme/repo#1: title**\n\n**Body (quoted):**\n> Includes Error handling details"

    monkeypatch.setattr("src.github.github_get_pr", fake_github_get_pr)

    result = await execute_tool("github_get_pr", owner="acme", repo="repo", pull_number=1)
    assert result.success is True
