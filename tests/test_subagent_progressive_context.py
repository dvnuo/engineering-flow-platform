import pytest

from src.agents.subagent import SubAgent


@pytest.mark.asyncio
async def test_subagent_run_uses_run_chat_execution(monkeypatch):
    captured = {}

    async def _fake_run_chat_execution(**kwargs):
        captured.update(kwargs)
        return {"response": "subagent-result"}

    monkeypatch.setattr("src.agents.subagent.run_chat_execution", _fake_run_chat_execution)

    subagent = SubAgent(
        session_key="sub-s1",
        task="do work",
        model=None,
        thinking=None,
    )
    subagent._agent = object()

    await subagent._run()

    assert subagent.status == "completed"
    assert subagent.result == "subagent-result"
    assert captured["message"] == "do work"
    assert captured["session_id"] == "sub-s1"
    assert captured["user_name"] == "sub-s1"
    assert captured["track_usage"] is False
