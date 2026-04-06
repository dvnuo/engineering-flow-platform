import pytest

from src.runtime.adapter_executor import execute_adapter_action, execute_jira_workflow_action


@pytest.mark.asyncio
async def test_execute_adapter_action_runs_registered_jira_action(monkeypatch):
    async def _fake_get_issue(issue_key):
        return f"Issue {issue_key} details"

    monkeypatch.setattr("src.jira.jira_get_issue", _fake_get_issue)

    result = await execute_adapter_action("adapter:jira:read_issue", {"issue_key": "PROJ-1"})

    assert result["success"] is True
    assert result["action_id"] == "adapter:jira:read_issue"
    assert result["system"] == "jira"
    assert len(result["runtime_events"]) >= 2


@pytest.mark.asyncio
async def test_execute_adapter_action_unknown_action_returns_error():
    result = await execute_adapter_action("adapter:jira:unknown", {"issue_key": "PROJ-1"})

    assert result["success"] is False
    assert "Unsupported adapter action" in result["error"]


@pytest.mark.asyncio
async def test_execute_jira_workflow_action_transition_issue(monkeypatch):
    async def _fake_transition(issue_key, to_status, comment=None):
        return f"{issue_key} transitioned to {to_status}"

    monkeypatch.setattr("src.jira.jira_transition", _fake_transition)

    result = await execute_jira_workflow_action(
        "transition_issue",
        {"issue_key": "PROJ-2", "transition": "Done", "comment": "done"},
    )

    assert result["success"] is True
    assert result["system"] == "jira"
    assert result["action_name"] == "transition_issue"


@pytest.mark.asyncio
async def test_execute_adapter_action_add_comment(monkeypatch):
    async def _fake_add_comment(issue_key, comment):
        return f"Comment added to {issue_key}: {comment}"

    monkeypatch.setattr("src.jira.jira_add_comment", _fake_add_comment)

    result = await execute_adapter_action(
        "adapter:jira:add_comment",
        {"issue_key": "PROJ-4", "comment": "Looks good"},
    )

    assert result["success"] is True
    assert result["action_id"] == "adapter:jira:add_comment"


@pytest.mark.asyncio
async def test_execute_adapter_action_github_review_pull_request(monkeypatch):
    async def _fake_get_pr(owner, repo, pull_number):
        return f"PR {owner}/{repo}#{pull_number}"

    async def _fake_get_pr_files(owner, repo, pull_number):
        return "files changed"

    async def _fake_get_pr_comments(owner, repo, pull_number):
        return "existing comments"

    monkeypatch.setattr("src.github.github_get_pr", _fake_get_pr)
    monkeypatch.setattr("src.github.github_get_pr_files", _fake_get_pr_files)
    monkeypatch.setattr("src.github.github_get_pr_comments", _fake_get_pr_comments)

    result = await execute_adapter_action(
        "adapter:github:review_pull_request",
        {"owner": "acme", "repo": "demo", "pull_number": 12},
    )

    assert result["success"] is True
    assert result["action_id"] == "adapter:github:review_pull_request"
    assert result["system"] == "github"
    assert "Automated review summary" in result["result"]["summary"]


@pytest.mark.asyncio
async def test_execute_adapter_action_github_unsupported_action_failed():
    result = await execute_adapter_action("adapter:github:not_supported", {"owner": "acme"})
    assert result["success"] is False
    assert result["system"] == "github"
    assert "Unsupported adapter action" in result["error"]
