import json

import pytest


@pytest.mark.asyncio
async def test_executor_execute_skill_uses_execute_skill_orchestration(monkeypatch):
    from src.agents import executor

    called = {}

    async def _fake_execute_skill_orchestration(**kwargs):
        called.update(kwargs)
        return type(
            "R",
            (),
            {
                "status": "success",
                "output_payload": {"output": "ok", "error": None, "data": {"k": "v"}},
            },
        )()

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.execute_skill_orchestration", _fake_execute_skill_orchestration)

    result = await executor.execute_skill("skill_x", session_id="s-1", foo="bar")

    assert result.success is True
    assert result.output == "ok"
    assert called["source_ref"] == "executor.execute_skill"
    assert called["session_id"] == "s-1"
    assert called["input_payload"]["skill_name"] == "skill_x"
    assert called["input_payload"]["kwargs"]["foo"] == "bar"


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
