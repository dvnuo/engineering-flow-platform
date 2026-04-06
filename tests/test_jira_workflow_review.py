import pytest

from src.agents.executor import SkillResult
from src.runtime.jira_workflow_review import run_jira_workflow_review


@pytest.mark.asyncio
async def test_jira_workflow_review_skill_success_applies_transition_and_reassign(monkeypatch):
    calls = []

    async def _fake_execute_jira_workflow_action(action_name, kwargs):
        calls.append((action_name, dict(kwargs)))
        if action_name == "read_issue":
            return {
                "success": True,
                "result": {"key": kwargs["issue_key"], "fields": {"reporter": {"accountId": "rep-1"}}},
                "error": None,
            }
        return {"success": True, "result": f"{action_name}:ok", "error": None}

    async def _fake_run_skill_execution(skill_name, **kwargs):
        assert skill_name == "review_skill"
        return SkillResult(
            success=True,
            output="approved",
            data={
                "workflow_outcome": {
                    "approved": True,
                    "decision": "approved",
                    "summary": "Looks good",
                    "comment": "Automated review approved",
                }
            },
        )

    monkeypatch.setattr("src.runtime.jira_workflow_review.execute_jira_workflow_action", _fake_execute_jira_workflow_action)
    monkeypatch.setattr("src.runtime.jira_workflow_review.run_skill_execution", _fake_run_skill_execution)

    result = await run_jira_workflow_review(
        {
            "issue_key": "PROJ-9",
            "skill_name": "review_skill",
            "success_transition": "Done",
            "success_reassign_to": "reporter",
            "fields_on_success": {"summary": "Approved summary"},
        }
    )

    assert result["success"] is True
    assert result["workflow_outcome"] == "approved"
    assert result["approved"] is True
    assert result["transitioned_to"] == "Done"
    assert result["assignee_updated"] == "rep-1"
    action_names = [name for name, _ in calls]
    assert action_names == ["read_issue", "add_comment", "update_issue", "transition_issue", "assign_issue"]


@pytest.mark.asyncio
async def test_jira_workflow_review_failure_path_reassigns_without_success_transition(monkeypatch):
    calls = []

    async def _fake_execute_jira_workflow_action(action_name, kwargs):
        calls.append((action_name, dict(kwargs)))
        if action_name == "read_issue":
            return {"success": True, "result": {"key": kwargs["issue_key"]}, "error": None}
        return {"success": True, "result": f"{action_name}:ok", "error": None}

    async def _fake_run_skill_execution(skill_name, **kwargs):
        return SkillResult(
            success=True,
            output="needs changes",
            data={"decision": "rejected", "comment": "Need changes"},
        )

    monkeypatch.setattr("src.runtime.jira_workflow_review.execute_jira_workflow_action", _fake_execute_jira_workflow_action)
    monkeypatch.setattr("src.runtime.jira_workflow_review.run_skill_execution", _fake_run_skill_execution)

    result = await run_jira_workflow_review(
        {
            "issue_key": "PROJ-10",
            "skill_name": "review_skill",
            "failure_transition": "In Progress",
            "explicit_failure_assignee": "dev-owner",
        }
    )

    assert result["success"] is True
    assert result["workflow_outcome"] == "rejected"
    assert result["approved"] is False
    assert result["transitioned_to"] == "In Progress"
    assert result["assignee_updated"] == "dev-owner"
    action_names = [name for name, _ in calls]
    assert "transition_issue" in action_names
    assert "assign_issue" in action_names


@pytest.mark.asyncio
async def test_requester_resolution_from_issue_snapshot(monkeypatch):
    async def _fake_execute_jira_workflow_action(action_name, kwargs):
        if action_name == "read_issue":
            return {
                "success": True,
                "result": {"key": kwargs["issue_key"], "fields": {"requester": {"accountId": "req-1"}}},
                "error": None,
            }
        return {"success": True, "result": "ok", "error": None}

    async def _fake_run_skill_execution(skill_name, **kwargs):
        return SkillResult(success=True, output="ok", data={"approved": True})

    monkeypatch.setattr("src.runtime.jira_workflow_review.execute_jira_workflow_action", _fake_execute_jira_workflow_action)
    monkeypatch.setattr("src.runtime.jira_workflow_review.run_skill_execution", _fake_run_skill_execution)

    result = await run_jira_workflow_review(
        {
            "issue_key": "PROJ-11",
            "skill_name": "review_skill",
            "success_reassign_to": "requester",
        }
    )

    assert result["success"] is True
    assert result["assignee_updated"] == "req-1"


@pytest.mark.asyncio
async def test_missing_structured_outcome_from_skill_fails(monkeypatch):
    async def _fake_execute_jira_workflow_action(action_name, kwargs):
        if action_name == "read_issue":
            return {"success": True, "result": {"key": kwargs["issue_key"]}, "error": None}
        return {"success": True, "result": "ok", "error": None}

    async def _fake_run_skill_execution(skill_name, **kwargs):
        return SkillResult(success=True, output="ok", data={"unrelated": True})

    monkeypatch.setattr("src.runtime.jira_workflow_review.execute_jira_workflow_action", _fake_execute_jira_workflow_action)
    monkeypatch.setattr("src.runtime.jira_workflow_review.run_skill_execution", _fake_run_skill_execution)

    result = await run_jira_workflow_review({"issue_key": "PROJ-12", "skill_name": "review_skill"})

    assert result["success"] is False
    assert "structured workflow outcome" in result["error"]


@pytest.mark.asyncio
async def test_backward_compatible_direct_payload_path_without_skill_name(monkeypatch):
    async def _fake_execute_jira_workflow_action(action_name, kwargs):
        if action_name == "read_issue":
            return {"success": True, "result": {"key": kwargs["issue_key"]}, "error": None}
        return {"success": True, "result": "ok", "error": None}

    monkeypatch.setattr("src.runtime.jira_workflow_review.execute_jira_workflow_action", _fake_execute_jira_workflow_action)

    result = await run_jira_workflow_review(
        {
            "issue_key": "PROJ-13",
            "review_comment": "legacy comment",
            "transition": "Done",
            "assignee": "legacy-owner",
            "fields": {"summary": "legacy"},
        }
    )

    assert result["success"] is True
    assert result["workflow_outcome"] == "approved"
    assert result["transitioned_to"] == "Done"
    assert result["assignee_updated"] == "legacy-owner"
