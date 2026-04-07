import pytest

from src.runtime.leader_delegation_adapter import (
    create_task_agent_for_group,
    create_specialist_delegation,
    create_task_agent_delegation,
    normalize_leader_delegation_request,
)
from src.runtime.leader_orchestration import (
    aggregate_delegation_results,
    build_delegation_requests_from_task_breakdown,
    dispatch_task_breakdown_as_delegations,
    evaluate_completion_criteria,
    load_coordination_run_state,
    run_delegation_cycle,
    select_assignee_for_task,
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


def test_normalize_leader_delegation_request_preserves_run_metadata():
    payload = normalize_leader_delegation_request(
        {
            "group_id": "g-1",
            "leader_agent_id": "leader-1",
            "assignee_agent_id": "a-1",
            "objective": "Review",
            "coordination_run_id": "coord-1",
            "round_index": 2,
        }
    )
    assert payload["coordination_run_id"] == "coord-1"
    assert payload["round_index"] == 2


def test_normalize_leader_delegation_request_preserves_task_mode_fields():
    payload = normalize_leader_delegation_request(
        {
            "group_id": "g-1",
            "leader_agent_id": "leader-1",
            "assignee_agent_id": "a-1",
            "objective": "Review",
            "agent_mode": "task",
            "skill_name": "custom_skill",
            "selection_strategy": "least_loaded",
            "template_agent_id": "tmpl-1",
            "task_agent_template_id": "tmpl-1",
            "ephemeral_task_agent_id": "ta-1",
            "task_agent_scope": "scope-a",
            "task_agent_scope_label": "scope-a",
            "task_agent_cleanup_policy": "delete_on_terminal",
            "scope_label": "scope-a",
            "name": "Task Agent A",
        }
    )
    assert payload["agent_mode"] == "task"
    assert payload["skill_name"] == "custom_skill"
    assert payload["task_agent_scope"] == "scope-a"
    assert payload["task_agent_cleanup_policy"] == "delete_on_terminal"


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
        assert kwargs["skill_name"] == "delegation"
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
            "skill_name": "delegation",
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
            {
                "assignee_agent_id": "a-2",
                "objective": "Task 2",
                "agent_mode": "task",
                "ephemeral_task_agent_id": "ta-existing",
                "task_agent_scope": "scope:existing",
            },
        ],
    )
    assert len(requests) == 2
    assert requests[0]["group_id"] == "g-1"
    assert requests[0]["leader_session_id"] == "s-1"
    assert requests[1]["agent_mode"] == "task"
    assert requests[1]["ephemeral_task_agent_id"] == "ta-existing"
    assert requests[1]["task_agent_scope"] == "scope:existing"
    assert requests[1]["skill_name"] == "delegation"


@pytest.mark.asyncio
async def test_dispatch_task_breakdown_as_delegations_returns_batch_result(monkeypatch):
    calls = {"specialist": 0, "task": 0}

    async def _fake_specialist(payload):
        calls["specialist"] += 1
        return {"success": True, "delegation_id": f"d-{payload['assignee_agent_id']}", "result": {}, "error": None}

    async def _fake_task(payload):
        calls["task"] += 1
        assert payload["agent_mode"] == "task"
        assert payload["skill_name"] == "delegation"
        return {"success": False, "delegation_id": None, "result": None, "error": "failed"}

    monkeypatch.setattr("src.runtime.leader_orchestration.create_specialist_delegation", _fake_specialist)
    monkeypatch.setattr("src.runtime.leader_orchestration.create_task_agent_delegation", _fake_task)
    result = await dispatch_task_breakdown_as_delegations(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[
            {"assignee_agent_id": "a-1", "objective": "Task 1"},
            {
                "assignee_agent_id": "a-2",
                "objective": "Task 2",
                "agent_mode": "task",
                "ephemeral_task_agent_id": "ta-existing",
                "task_agent_scope": "scope:existing",
            },
        ],
    )
    assert result["created"] == 1
    assert result["failed"] == 1
    assert len(result["items"]) == 2
    assert calls["specialist"] == 1
    assert calls["task"] == 1


@pytest.mark.asyncio
async def test_dispatch_task_breakdown_collects_deleted_task_agent_ids_from_cleanup_metadata(monkeypatch):
    async def _fake_specialist(payload):
        return {
            "success": True,
            "delegation_id": "d-1",
            "result": {"cleanup": {"deleted_task_agent_ids": ["ta-1"]}, "delegation_result": {"deleted_task_agent_ids": ["ta-2"]}},
            "error": None,
        }

    monkeypatch.setattr("src.runtime.leader_orchestration.create_specialist_delegation", _fake_specialist)
    result = await dispatch_task_breakdown_as_delegations(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[{"assignee_agent_id": "a-1", "objective": "Task 1"}],
    )
    assert result["deleted_task_agent_ids"] == ["ta-1", "ta-2"]


def test_aggregate_delegation_results_done_and_blockers():
    aggregate_done = aggregate_delegation_results(
        [
            {"result": {"assignee_agent_id": "a-1", "status": "done"}},
            {"result": {"assignee_agent_id": "a-2", "status": "done"}},
        ]
    )
    assert aggregate_done["all_done"] is True
    aggregate_blocked = aggregate_delegation_results(
        [
            {"result": {"assignee_agent_id": "a-1", "status": "done", "blockers": ["missing_data"]}},
        ]
    )
    assert aggregate_blocked["has_blockers"] is True
    assert "missing_data" in aggregate_blocked["blockers"]


def test_evaluate_completion_criteria_default_and_blocked():
    incomplete = evaluate_completion_criteria(None, {"all_done": False, "has_blockers": False})
    assert incomplete["is_complete"] is False
    blocked = evaluate_completion_criteria({"mode": "all_done"}, {"all_done": True, "has_blockers": True})
    assert blocked["is_complete"] is False
    assert blocked["reason"] == "blockers_present"
    in_progress = evaluate_completion_criteria({"mode": "all_done"}, {"all_done": False, "has_blockers": False, "status_by_assignee": {"a1": "running"}})
    assert in_progress["reason"] == "work_in_progress"
    failed = evaluate_completion_criteria({"mode": "all_done"}, {"all_done": False, "has_blockers": False, "status_by_assignee": {"a1": "failed"}})
    assert failed["reason"] == "failed_terminal"
    run_blocked = evaluate_completion_criteria({"mode": "all_done"}, {"all_done": False, "has_blockers": False, "run_status": "blocked"})
    assert run_blocked["reason"] == "run_blocked"
    run_done = evaluate_completion_criteria({"mode": "all_done"}, {"run_status": "done", "all_terminal": True, "all_done": True})
    assert run_done["is_complete"] is True
    run_failed = evaluate_completion_criteria({"mode": "all_done"}, {"run_status": "failed", "all_terminal": True, "all_done": False})
    assert run_failed["reason"] == "failed_terminal"


@pytest.mark.asyncio
async def test_run_delegation_cycle_next_action_continue_complete_blocked(monkeypatch):
    async def _fake_dispatch(**_kwargs):
        return {"success": True, "created": 1, "failed": 0, "items": [{"result": {"assignee_agent_id": "a-1", "status": "in_progress"}}]}

    async def _fake_load(**_kwargs):
        return {
            "coordination_run_id": "coord-1",
            "status": "running",
            "summary": {"status_counts": {"queued": 0, "running": 1, "done": 0, "failed": 0, "other": 0}},
            "rounds": [1],
            "delegations": [{"result": {"assignee_agent_id": "a-1", "status": "in_progress"}}],
            "status_counts": {"queued": 0, "running": 1, "done": 0, "failed": 0, "other": 0},
            "latest_round_index": 1,
            "all_terminal": False,
            "all_done": False,
            "has_blockers": False,
            "completed_at": None,
        }

    monkeypatch.setattr("src.runtime.leader_orchestration.dispatch_task_breakdown_as_delegations", _fake_dispatch)
    monkeypatch.setattr("src.runtime.leader_orchestration.load_coordination_run_state", _fake_load)
    result_continue = await run_delegation_cycle(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[{"assignee_agent_id": "a-1", "objective": "Task"}],
    )
    assert result_continue["coordination_run_id"].startswith("coord-")
    assert result_continue["round_index"] == 1
    assert result_continue["next_action"] == "continue"
    assert "leader_summary" in result_continue
    assert result_continue["leader_summary"]["run_status"] == "running"

    async def _fake_load_done(**_kwargs):
        return {
            "coordination_run_id": "coord-1",
            "status": "done",
            "summary": {"status_counts": {"queued": 0, "running": 0, "done": 1, "failed": 0, "other": 0}},
            "rounds": [1],
            "delegations": [{"result": {"assignee_agent_id": "a-1", "status": "done"}}],
            "status_counts": {"queued": 0, "running": 0, "done": 1, "failed": 0, "other": 0},
            "latest_round_index": 1,
            "all_terminal": True,
            "all_done": True,
            "has_blockers": False,
            "completed_at": "2026-04-07T00:00:00Z",
        }

    monkeypatch.setattr("src.runtime.leader_orchestration.load_coordination_run_state", _fake_load_done)
    result_complete = await run_delegation_cycle(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[],
        prior_results=[{"result": {"assignee_agent_id": "a-1", "status": "done"}}],
    )
    assert result_complete["next_action"] == "complete"
    assert result_complete["leader_summary"]["completed_at"] == "2026-04-07T00:00:00Z"

    async def _fake_load_blocked(**_kwargs):
        return {
            "coordination_run_id": "coord-1",
            "status": "blocked",
            "summary": {},
            "rounds": [1],
            "delegations": [{"result": {"assignee_agent_id": "a-1", "status": "done", "blockers": ["blocked"]}}],
            "status_counts": {"queued": 0, "running": 0, "done": 1, "failed": 0, "other": 0},
            "latest_round_index": 1,
            "all_terminal": False,
            "all_done": False,
            "has_blockers": True,
            "completed_at": None,
        }

    monkeypatch.setattr("src.runtime.leader_orchestration.load_coordination_run_state", _fake_load_blocked)
    result_blocked = await run_delegation_cycle(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[],
        prior_results=[{"result": {"assignee_agent_id": "a-1", "status": "done", "blockers": ["blocked"]}}],
    )
    assert result_blocked["next_action"] == "blocked"


@pytest.mark.asyncio
async def test_run_delegation_cycle_invalid_round_index_normalizes_to_one(monkeypatch):
    async def _fake_dispatch(**_kwargs):
        return {"success": True, "created": 0, "failed": 0, "items": []}

    monkeypatch.setattr("src.runtime.leader_orchestration.dispatch_task_breakdown_as_delegations", _fake_dispatch)
    result = await run_delegation_cycle(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        round_index=0,
        tasks=[{"assignee_agent_id": "a-1", "objective": "Task"}],
    )
    assert result["round_index"] == 1


@pytest.mark.asyncio
async def test_load_coordination_run_state_filters_and_counts(monkeypatch):
    async def _fake_execute(action_id, kwargs):
        if action_id == "adapter:portal:get_coordination_run":
            return {
                "success": True,
                "result": {
                    "coordination_run_id": kwargs["coordination_run_id"],
                    "status": "done",
                    "summary": {"status_counts": {"queued": 0, "running": 0, "done": 2, "failed": 0, "other": 0}},
                    "rounds": [1, 2],
                    "completed_at": "2026-04-07T00:00:00Z",
                },
                "error": None,
            }
        raise AssertionError("fallback call should not be required")

    monkeypatch.setattr("src.runtime.leader_orchestration.execute_adapter_action", _fake_execute)
    state = await load_coordination_run_state(group_id="g-1", coordination_run_id="coord-1")
    assert state["latest_round_index"] == 2
    assert state["status_counts"]["done"] == 2
    assert state["status"] == "done"
    assert state["all_terminal"] is True
    assert state["all_done"] is True


@pytest.mark.asyncio
async def test_load_coordination_run_state_falls_back_to_delegations(monkeypatch):
    async def _fake_execute(action_id, kwargs):
        if action_id == "adapter:portal:get_coordination_run":
            return {"success": False, "result": None, "error": "not found"}
        assert action_id == "adapter:portal:list_group_delegations"
        assert kwargs["group_id"] == "g-1"
        return {
            "success": True,
            "result": {
                "delegations": [
                    {"coordination_run_id": "coord-1", "round_index": 1, "status": "queued"},
                    {"coordination_run_id": "coord-1", "round_index": 2, "status": "done"},
                    {"coordination_run_id": "coord-1", "round_index": 2, "status": "failed"},
                    {"coordination_run_id": "coord-2", "round_index": 1, "status": "done"},
                ]
            },
            "error": None,
        }

    monkeypatch.setattr("src.runtime.leader_orchestration.execute_adapter_action", _fake_execute)
    state = await load_coordination_run_state(group_id="g-1", coordination_run_id="coord-1")
    assert state["status_counts"]["queued"] == 1
    assert state["status_counts"]["done"] == 1
    assert state["status_counts"]["failed"] == 1


@pytest.mark.asyncio
async def test_run_delegation_cycle_evaluation_mode_loads_run_state(monkeypatch):
    async def _fake_load(*, group_id, coordination_run_id):
        assert group_id == "g-1"
        return {
            "coordination_run_id": coordination_run_id,
            "status": "running",
            "summary": {},
            "rounds": [1, 2],
            "delegations": [{"assignee_agent_id": "a-1", "status": "running"}],
            "status_counts": {"queued": 0, "running": 1, "done": 0, "failed": 0, "other": 0},
            "latest_round_index": 2,
            "all_terminal": False,
            "all_done": False,
            "has_blockers": False,
            "completed_at": None,
        }

    monkeypatch.setattr("src.runtime.leader_orchestration.load_coordination_run_state", _fake_load)
    result = await run_delegation_cycle(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        coordination_run_id="coord-abc",
        tasks=[],
    )
    assert result["run_state"]["latest_round_index"] == 2
    assert result["next_action"] == "continue"
    assert result["leader_summary"]["run_status"] == "running"


@pytest.mark.asyncio
async def test_run_delegation_cycle_merges_deleted_task_agent_ids_from_dispatch_and_run_state(monkeypatch):
    async def _fake_dispatch(**_kwargs):
        return {"success": True, "created": 1, "failed": 0, "items": [], "deleted_task_agent_ids": ["ta-dispatch"]}

    async def _fake_load_state(*_args, **_kwargs):
        return {
            "coordination_run_id": "coord-1",
            "status": "running",
            "summary": {"deleted_task_agent_ids": ["ta-summary"]},
            "delegations": [{"cleanup_deleted_task_agent_id": "ta-delegation"}],
            "status_counts": {"queued": 0, "running": 1, "done": 0, "failed": 0, "other": 0},
            "rounds": [1],
            "latest_round_index": 1,
            "all_terminal": False,
            "all_done": False,
            "has_blockers": False,
            "completed_at": None,
        }

    async def _fake_board(_group_id):
        return {"effective_max_parallel_tasks": None, "active_parallel_tasks": 0}

    monkeypatch.setattr("src.runtime.leader_orchestration.dispatch_task_breakdown_as_delegations", _fake_dispatch)
    monkeypatch.setattr("src.runtime.leader_orchestration.load_coordination_run_state", _fake_load_state)
    monkeypatch.setattr("src.runtime.leader_orchestration._load_group_parallelism_budget", _fake_board)
    result = await run_delegation_cycle(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[{"assignee_agent_id": "a-1", "objective": "Task"}],
    )
    assert result["deleted_task_agent_ids"] == ["ta-dispatch", "ta-summary", "ta-delegation"]


@pytest.mark.asyncio
async def test_run_delegation_cycle_throttles_dispatch_when_no_parallel_slots(monkeypatch):
    async def _fake_dispatch(**_kwargs):
        raise AssertionError("dispatch should not be called when no slots are available")

    async def _fake_load_state(*_args, **_kwargs):
        return {
            "coordination_run_id": "coord-1",
            "status": "running",
            "summary": {},
            "delegations": [],
            "status_counts": {"queued": 0, "running": 1, "done": 0, "failed": 0, "other": 0},
            "rounds": [1],
            "latest_round_index": 1,
            "all_terminal": False,
            "all_done": False,
            "has_blockers": False,
            "completed_at": None,
        }

    async def _fake_board(_group_id):
        return {"effective_max_parallel_tasks": 2, "active_parallel_tasks": 2}

    monkeypatch.setattr("src.runtime.leader_orchestration.dispatch_task_breakdown_as_delegations", _fake_dispatch)
    monkeypatch.setattr("src.runtime.leader_orchestration.load_coordination_run_state", _fake_load_state)
    monkeypatch.setattr("src.runtime.leader_orchestration._load_group_parallelism_budget", _fake_board)
    result = await run_delegation_cycle(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[{"assignee_agent_id": "a-1", "objective": "Task"}],
    )
    assert result["created"] == 0
    assert len(result["deferred_tasks"]) == 1
    assert result["effective_max_parallel_tasks"] == 2
    assert result["active_parallel_tasks"] == 2


@pytest.mark.asyncio
async def test_run_delegation_cycle_throttles_dispatch_partial_slots(monkeypatch):
    captured = {}

    async def _fake_dispatch(**kwargs):
        captured["tasks"] = kwargs["tasks"]
        return {"success": True, "created": len(kwargs["tasks"]), "failed": 0, "items": []}

    async def _fake_load_state(*_args, **_kwargs):
        return {
            "coordination_run_id": "coord-1",
            "status": "running",
            "summary": {},
            "delegations": [],
            "status_counts": {"queued": 0, "running": 1, "done": 0, "failed": 0, "other": 0},
            "rounds": [1],
            "latest_round_index": 1,
            "all_terminal": False,
            "all_done": False,
            "has_blockers": False,
            "completed_at": None,
        }

    async def _fake_board(_group_id):
        return {"effective_max_parallel_tasks": 2, "active_parallel_tasks": 1}

    monkeypatch.setattr("src.runtime.leader_orchestration.dispatch_task_breakdown_as_delegations", _fake_dispatch)
    monkeypatch.setattr("src.runtime.leader_orchestration.load_coordination_run_state", _fake_load_state)
    monkeypatch.setattr("src.runtime.leader_orchestration._load_group_parallelism_budget", _fake_board)
    result = await run_delegation_cycle(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[
            {"assignee_agent_id": "a-1", "objective": "Task 1"},
            {"assignee_agent_id": "a-2", "objective": "Task 2"},
        ],
    )
    assert len(captured["tasks"]) == 1
    assert len(result["deferred_tasks"]) == 1
    assert result["created"] == 1


@pytest.mark.asyncio
async def test_run_delegation_cycle_without_parallel_policy_keeps_behavior(monkeypatch):
    captured = {}

    async def _fake_dispatch(**kwargs):
        captured["tasks"] = kwargs["tasks"]
        return {"success": True, "created": len(kwargs["tasks"]), "failed": 0, "items": []}

    async def _fake_load_state(*_args, **_kwargs):
        return {
            "coordination_run_id": "coord-1",
            "status": "running",
            "summary": {},
            "delegations": [],
            "status_counts": {"queued": 0, "running": 1, "done": 0, "failed": 0, "other": 0},
            "rounds": [1],
            "latest_round_index": 1,
            "all_terminal": False,
            "all_done": False,
            "has_blockers": False,
            "completed_at": None,
        }

    async def _fake_board(_group_id):
        return {"effective_max_parallel_tasks": None, "active_parallel_tasks": 0}

    monkeypatch.setattr("src.runtime.leader_orchestration.dispatch_task_breakdown_as_delegations", _fake_dispatch)
    monkeypatch.setattr("src.runtime.leader_orchestration.load_coordination_run_state", _fake_load_state)
    monkeypatch.setattr("src.runtime.leader_orchestration._load_group_parallelism_budget", _fake_board)
    result = await run_delegation_cycle(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[
            {"assignee_agent_id": "a-1", "objective": "Task 1"},
            {"assignee_agent_id": "a-2", "objective": "Task 2"},
        ],
    )
    assert len(captured["tasks"]) == 2
    assert result["deferred_tasks"] == []


@pytest.mark.asyncio
async def test_select_assignee_for_task_least_loaded(monkeypatch):
    async def _fake_pool(group_id):
        assert group_id == "g-1"
        return {"success": True, "result": {"items": [{"agent_id": "a-2"}, {"agent_id": "a-1"}, {"agent_id": "leader-1"}]}}

    async def _fake_execute(action_id, kwargs):
        assert action_id == "adapter:portal:list_group_delegations"
        assert kwargs["group_id"] == "g-1"
        return {
            "success": True,
            "result": {
                "delegations": [
                    {"assignee_agent_id": "a-1", "status": "queued"},
                    {"assignee_agent_id": "a-1", "status": "running"},
                ]
            },
        }

    monkeypatch.setattr("src.runtime.leader_orchestration.get_group_specialist_pool", _fake_pool)
    monkeypatch.setattr("src.runtime.leader_orchestration.execute_adapter_action", _fake_execute)
    result = await select_assignee_for_task(
        group_id="g-1",
        leader_agent_id="leader-1",
        task={"objective": "x", "selection_strategy": "least_loaded"},
    )
    assert result["success"] is True
    assert result["assignee_agent_id"] == "a-2"


@pytest.mark.asyncio
async def test_select_assignee_for_task_supports_specialist_agent_ids_shape(monkeypatch):
    async def _fake_pool(group_id):
        return {"success": True, "result": {"group_id": group_id, "specialist_agent_ids": ["a-2", "a-1", "leader-1"]}}

    async def _fake_execute(_action_id, _kwargs):
        return {"success": True, "result": {"delegations": [{"assignee_agent_id": "a-1", "status": "running"}]}}

    monkeypatch.setattr("src.runtime.leader_orchestration.get_group_specialist_pool", _fake_pool)
    monkeypatch.setattr("src.runtime.leader_orchestration.execute_adapter_action", _fake_execute)
    result = await select_assignee_for_task(
        group_id="g-1",
        leader_agent_id="leader-1",
        task={"objective": "x", "selection_strategy": "least_loaded"},
    )
    assert result["assignee_agent_id"] == "a-2"


@pytest.mark.asyncio
async def test_select_assignee_for_task_tie_break_lexical(monkeypatch):
    async def _fake_pool(group_id):
        return {"success": True, "result": {"items": [{"agent_id": "b-agent"}, {"agent_id": "a-agent"}]}}

    async def _fake_execute(_action_id, _kwargs):
        return {"success": True, "result": {"delegations": []}}

    monkeypatch.setattr("src.runtime.leader_orchestration.get_group_specialist_pool", _fake_pool)
    monkeypatch.setattr("src.runtime.leader_orchestration.execute_adapter_action", _fake_execute)
    result = await select_assignee_for_task(
        group_id="g-1",
        leader_agent_id="leader-1",
        task={"objective": "x", "selection_strategy": "least_loaded"},
    )
    assert result["assignee_agent_id"] == "a-agent"


@pytest.mark.asyncio
async def test_dispatch_task_breakdown_as_delegations_task_agent_auto_create_missing_template_errors():
    with pytest.raises(ValueError, match="template_agent_id is required"):
        await dispatch_task_breakdown_as_delegations(
            group_id="g-1",
            leader_agent_id="leader-1",
            leader_session_id="s-1",
            tasks=[{"agent_mode": "task", "objective": "Task"}],
        )


@pytest.mark.asyncio
async def test_dispatch_task_breakdown_as_delegations_task_agent_auto_create_injects_assignee(monkeypatch):
    async def _fake_pool(_group_id):
        return {"success": True, "result": {"items": [{"agent_id": "a-1"}]}}

    async def _fake_list(action_id, kwargs):
        assert action_id == "adapter:portal:list_group_delegations"
        return {"success": True, "result": {"delegations": []}}

    async def _fake_create_task_agent(payload):
        assert payload["group_id"] == "g-1"
        assert payload["leader_agent_id"] == "leader-1"
        assert payload["template_agent_id"] == "tmpl-1"
        assert payload["task_agent_cleanup_policy"] == "delete_on_terminal"
        return {"success": True, "result": {"agent_id": "ta-1"}}

    async def _fake_task_delegate(payload):
        assert payload["assignee_agent_id"] == "ta-1"
        assert payload["ephemeral_task_agent_id"] == "ta-1"
        assert payload["task_agent_scope"] == "scope-a"
        assert payload["task_agent_scope_label"] == "scope-a"
        assert payload["task_agent_cleanup_policy"] == "delete_on_terminal"
        assert payload["skill_kwargs"]["agent_mode"] == "task"
        assert payload["skill_kwargs"]["scope_label"] == "scope-a"
        assert payload["skill_kwargs"]["cleanup_policy"] == "delete_on_terminal"
        assert payload["skill_kwargs"]["task_agent_template_id"] == "tmpl-1"
        return {"success": True, "delegation_id": "d-1", "result": {"delegation_id": "d-1"}, "error": None}

    monkeypatch.setattr("src.runtime.leader_orchestration.get_group_specialist_pool", _fake_pool)
    monkeypatch.setattr("src.runtime.leader_orchestration.execute_adapter_action", _fake_list)
    monkeypatch.setattr("src.runtime.leader_orchestration.create_task_agent_for_group", _fake_create_task_agent)
    monkeypatch.setattr("src.runtime.leader_orchestration.create_task_agent_delegation", _fake_task_delegate)

    result = await dispatch_task_breakdown_as_delegations(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[
            {
                "agent_mode": "task",
                "selection_strategy": "least_loaded",
                "objective": "Task",
                "template_agent_id": "tmpl-1",
                "scope_label": "scope-a",
            }
        ],
    )
    assert result["success"] is True
    assert result["created_task_agent_ids"] == ["ta-1"]
    assert result["auto_selected_assignee_ids"] == []


@pytest.mark.asyncio
async def test_dispatch_task_breakdown_task_mode_template_auto_create_without_assignee_or_selection(monkeypatch):
    async def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("select_assignee_for_task should not be called for task auto-create path")

    async def _fake_create_task_agent(payload):
        assert payload["leader_agent_id"] == "leader-1"
        assert payload["template_agent_id"] == "tmpl-1"
        assert payload["task_agent_cleanup_policy"] == "retain"
        return {"success": True, "result": {"agent_id": "ta-created"}}

    async def _fake_task_delegate(payload):
        assert payload["assignee_agent_id"] == "ta-created"
        assert payload["ephemeral_task_agent_id"] == "ta-created"
        assert payload["task_agent_cleanup_policy"] == "retain"
        assert payload["skill_kwargs"]["cleanup_policy"] == "retain"
        return {"success": True, "delegation_id": "d-created", "result": {"delegation_id": "d-created"}, "error": None}

    monkeypatch.setattr("src.runtime.leader_orchestration.select_assignee_for_task", _raise_if_called)
    monkeypatch.setattr("src.runtime.leader_orchestration.create_task_agent_for_group", _fake_create_task_agent)
    monkeypatch.setattr("src.runtime.leader_orchestration.create_task_agent_delegation", _fake_task_delegate)

    result = await dispatch_task_breakdown_as_delegations(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[{"agent_mode": "task", "template_agent_id": "tmpl-1", "objective": "Task", "task_agent_cleanup_policy": "retain"}],
    )
    assert result["success"] is True
    assert result["created_task_agent_ids"] == ["ta-created"]
    assert result["auto_selected_assignee_ids"] == []


@pytest.mark.asyncio
async def test_dispatch_task_breakdown_task_mode_existing_ephemeral_bypasses_selection(monkeypatch):
    async def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("select_assignee_for_task should not be called for existing ephemeral task agent")

    async def _fake_task_delegate(payload):
        assert payload["assignee_agent_id"] == "ta-existing"
        assert payload["ephemeral_task_agent_id"] == "ta-existing"
        return {"success": True, "delegation_id": "d-ephemeral", "result": {"delegation_id": "d-ephemeral"}, "error": None}

    monkeypatch.setattr("src.runtime.leader_orchestration.select_assignee_for_task", _raise_if_called)
    monkeypatch.setattr("src.runtime.leader_orchestration.create_task_agent_delegation", _fake_task_delegate)

    result = await dispatch_task_breakdown_as_delegations(
        group_id="g-1",
        leader_agent_id="leader-1",
        leader_session_id="s-1",
        tasks=[{"agent_mode": "task", "objective": "Task", "ephemeral_task_agent_id": "ta-existing"}],
    )
    assert result["success"] is True
    assert result["created_task_agent_ids"] == []
    assert result["auto_selected_assignee_ids"] == []


@pytest.mark.asyncio
async def test_dispatch_task_breakdown_task_mode_blank_leader_agent_id_fails_before_create():
    with pytest.raises(ValueError, match="leader_agent_id is required for task agent creation"):
        await dispatch_task_breakdown_as_delegations(
            group_id="g-1",
            leader_agent_id="",
            leader_session_id="s-1",
            tasks=[{"agent_mode": "task", "template_agent_id": "tmpl-1", "objective": "Task"}],
        )


@pytest.mark.asyncio
async def test_create_task_agent_for_group_missing_required_fields():
    missing_leader = await create_task_agent_for_group(
        {"group_id": "g-1", "template_agent_id": "tmpl-1", "name": "ta-1"}
    )
    assert missing_leader["success"] is False
    assert "leader_agent_id" in missing_leader["error"]

    missing_template = await create_task_agent_for_group(
        {"group_id": "g-1", "leader_agent_id": "leader-1", "name": "ta-1"}
    )
    assert missing_template["success"] is False
    assert "template_agent_id" in missing_template["error"]
