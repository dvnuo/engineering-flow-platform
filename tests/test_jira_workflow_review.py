import pytest

from src.runtime.jira_workflow_review import run_jira_workflow_review


@pytest.mark.asyncio
async def test_jira_workflow_review_reads_and_applies_actions(monkeypatch):
    async def _fake_execute_jira_workflow_action(action_name, kwargs):
        if action_name == "read_issue":
            return {"success": True, "result": {"key": kwargs["issue_key"]}, "error": None}
        return {"success": True, "result": f"{action_name}:ok", "error": None}

    monkeypatch.setattr(
        "src.runtime.jira_workflow_review.execute_jira_workflow_action",
        _fake_execute_jira_workflow_action,
    )

    result = await run_jira_workflow_review(
        {
            "issue_key": "PROJ-9",
            "review_comment": "Looks good",
            "assignee": "alice",
            "transition": "Done",
            "fields": {"summary": "Updated"},
        }
    )

    assert result["success"] is True
    assert result["reviewed"] is True
    assert result["issue_snapshot"] == {"key": "PROJ-9"}
    assert result["comment_added"] is True
    assert result["assignee_updated"] == "alice"
    assert result["transitioned_to"] == "Done"
    assert result["updated_fields"] == {"summary": "Updated"}
    assert any(evt.get("event_type") == "task.jira_workflow_review.completed" for evt in result["runtime_events"])


@pytest.mark.asyncio
async def test_jira_workflow_review_missing_issue_key_returns_error():
    result = await run_jira_workflow_review({})

    assert result["success"] is False
    assert "issue_key is required" in result["error"]
    assert any(evt.get("event_type") == "task.jira_workflow_review.failed" for evt in result["runtime_events"])
