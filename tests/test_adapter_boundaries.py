import json
import inspect
import asyncio

import pytest


@pytest.mark.asyncio
async def test_skill_mode_generate_initial_skill_plan_uses_execute_skill_orchestration(monkeypatch):
    from src.agents import skill_mode
    from src.skills.registry import Skill

    captured = {}

    async def _fake_execute_skill_orchestration(**kwargs):
        captured.update(kwargs)
        return type(
            "R",
            (),
            {
                "status": "success",
                "output_payload": {
                    "goal": "g",
                    "steps": [{"id": "s1", "type": "execute", "title": "t1"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                },
            },
        )()

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.execute_skill_orchestration", _fake_execute_skill_orchestration)

    skill = Skill(name="demo_skill", description="demo desc")
    goal, steps, usage = await skill_mode.generate_initial_skill_plan(skill=skill, user_message="hello")

    assert goal == "g"
    assert steps and steps[0]["id"] == "s1"
    assert usage["total_tokens"] == 3
    assert captured["source_ref"] == "skill_mode.generate_initial_skill_plan"
    assert captured["session_id"] is None
    assert captured["input_payload"]["skill_name"] == "demo_skill"
    assert callable(captured["custom_skill_handler"])


def test_subagent_sessions_spawn_uses_execute_subagent_orchestration(monkeypatch):
    from src.agents import subagent

    captured = {}

    async def _fake_execute_subagent_orchestration(**kwargs):
        captured.update(kwargs)
        return type(
            "R",
            (),
            {
                "output_payload": {
                    "session_key": kwargs["session_id"],
                    "status": "started",
                }
            },
        )()

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.execute_subagent_orchestration", _fake_execute_subagent_orchestration)

    result_json = subagent.sessions_spawn(
        task="review code",
        model="gpt-5-mini",
        thinking="low",
        disable_tools=True,
        cleanup="keep",
        label="sub-1",
    )
    payload = json.loads(result_json)

    assert payload["session_key"] == "sub-1"
    forwarded = captured["input_payload"]
    assert forwarded["task"] == "review code"
    assert forwarded["session_key"] == "sub-1"
    assert forwarded["model"] == "gpt-5-mini"
    assert forwarded["thinking"] == "low"
    assert forwarded["disable_tools"] is True
    assert forwarded["cleanup"] == "keep"
    assert forwarded["start_immediately"] is False
    assert forwarded["wait_for_completion"] is False


@pytest.mark.asyncio
async def test_webchat_tasks_execute_uses_execute_runtime_task_request(monkeypatch):
    from src.gateway import webchat
    webchat.runtime_task_tracker.reset()

    captured = {}
    spawned = []

    async def _fake_execute_runtime_task_request(**kwargs):
        captured.update(kwargs)
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {"success": True},
                "artifacts": {},
                "runtime_events": [],
                "next_action_hint": None,
                "audit_ref": None,
            },
        )()

    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    class _Request:
        headers = {}
        async def json(self):
            return {
                "task_id": "task-rt-1",
                "task_type": "adapter_action_task",
                "shared_context_ref": "ctx://abc",
                "input_payload": {"action_id": "adapter:jira:read_issue", "kwargs": {"issue_key": "PROJ-1"}},
            }

    response = await webchat.api_tasks_execute(_Request())
    payload = json.loads(response.body)
    await spawned[0]

    assert response.status == 202
    assert payload["task_id"] == "task-rt-1"
    assert captured["request_id"] == "task-task-rt-1"
    assert captured["metadata"]["task_id"] == "task-rt-1"
    assert captured["metadata"]["portal_task_id"] == "task-rt-1"
    assert captured["metadata"]["shared_context_ref"] == "ctx://abc"


def test_entrypoints_do_not_reintroduce_direct_bus_construction():
    from src.agents import skill_mode, subagent
    from src.gateway import webchat

    sources = {
        "skill_mode": inspect.getsource(skill_mode),
        "subagent": inspect.getsource(subagent),
        "webchat": inspect.getsource(webchat),
    }
    forbidden = ("build_default_execution_bus(", "make_execution_request(")
    for name, source in sources.items():
        for token in forbidden:
            assert token not in source, f"{name} unexpectedly contains {token}"


def test_runtime_helper_modules_do_not_import_adapter_executor_directly():
    from src.runtime import jira_workflow_review, leader_delegation_adapter, leader_orchestration

    sources = {
        "leader_delegation_adapter": inspect.getsource(leader_delegation_adapter),
        "leader_orchestration": inspect.getsource(leader_orchestration),
        "jira_workflow_review": inspect.getsource(jira_workflow_review),
    }
    forbidden = "from src.runtime.adapter_executor import"
    for name, source in sources.items():
        assert forbidden not in source, f"{name} unexpectedly imports low-level adapter executor"
