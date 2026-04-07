import pytest

from src.runtime.leader_delegation_adapter import (
    create_specialist_delegation,
    create_task_agent_delegation,
    normalize_leader_delegation_request,
)
from src.runtime.leader_orchestration import (
    build_delegation_requests_from_task_breakdown,
    dispatch_task_breakdown_as_delegations,
)


def test_normalize_leader_delegation_request_defaults():
    payload = normalize_leader_delegation_request(
        {
            "group_id": "g-1",
            "leader_agent_id": "leader-1",
            "assignee_agent_id": "a-1",
            "objective": "Review",
        }
    )
    assert payload["visibility"] == "leader_only"
    assert payload["parent_agent_id"] == "leader-1"


@pytest.mark.asyncio
async def test_create_specialist_delegation_sets_agent_mode(monkeypatch):
    async def _fake_execute(_action_id, kwargs):
        assert kwargs["agent_mode"] == "specialist"
        return {"success": True, "result": {"delegation_id": "d-1"}, "error": None}

    monkeypatch.setattr("src.runtime.leader_delegation_adapter.execute_adapter_action", _fake_execute)
    result = await create_specialist_delegation(
        {
            "group_id": "g-1",
            "leader_agent_id": "leader-1",
            "assignee_agent_id": "a-1",
            "objective": "Review",
        }
    )
    assert result["success"] is True
    assert result["delegation_id"] == "d-1"


@pytest.mark.asyncio
async def test_create_task_agent_delegation_sets_mode_and_preserves_fields(monkeypatch):
    async def _fake_execute(_action_id, kwargs):
        assert kwargs["agent_mode"] == "task"
        assert kwargs["task_agent_template_id"] == "tmpl-1"
        assert kwargs["task_agent_cleanup_policy"] == "cleanup"
        return {"success": True, "result": {"delegation_id": "d-2"}, "error": None}

    monkeypatch.setattr("src.runtime.leader_delegation_adapter.execute_adapter_action", _fake_execute)
    result = await create_task_agent_delegation(
        {
            "group_id": "g-1",
            "leader_agent_id": "leader-1",
            "assignee_agent_id": "a-1",
            "objective": "Review",
            "ephemeral_task_agent_id": "task-agent-1",
            "task_agent_scope": "repo:acme/demo",
            "task_agent_template_id": "tmpl-1",
            "task_agent_cleanup_policy": "cleanup",
        }
    )
    assert result["success"] is True
    assert result["delegation_id"] == "d-2"


def test_build_delegation_requests_from_task_breakdown():
    requests = build_delegation_requests_from_task_breakdown(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[
            {"assignee_agent_id": "a-1", "objective": "Task 1"},
            {"assignee_agent_id": "a-2", "objective": "Task 2", "agent_mode": "task"},
        ],
    )
    assert len(requests) == 2
    assert requests[0]["group_id"] == "g-1"
    assert requests[0]["leader_session_id"] == "s-1"


@pytest.mark.asyncio
async def test_dispatch_task_breakdown_as_delegations_returns_batch_result(monkeypatch):
    async def _fake_specialist(payload):
        return {"success": True, "delegation_id": f"d-{payload['assignee_agent_id']}", "result": {}, "error": None}

    async def _fake_task(payload):
        return {"success": False, "delegation_id": None, "result": None, "error": "failed"}

    monkeypatch.setattr("src.runtime.leader_orchestration.create_specialist_delegation", _fake_specialist)
    monkeypatch.setattr("src.runtime.leader_orchestration.create_task_agent_delegation", _fake_task)
    result = await dispatch_task_breakdown_as_delegations(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[
            {"assignee_agent_id": "a-1", "objective": "Task 1"},
            {"assignee_agent_id": "a-2", "objective": "Task 2", "agent_mode": "task"},
        ],
    )
    assert result["created"] == 1
    assert result["failed"] == 1
    assert len(result["items"]) == 2

