import pytest

from src.agents.skill_mode import SkillSession, compact_skill_session_async


@pytest.mark.asyncio
async def test_compact_skill_session_async_uses_max_tokens_not_budget_tokens(monkeypatch):
    captured = {}

    async def _fake_compact_messages(**kwargs):
        captured.update(kwargs)
        return kwargs["messages"], type("Stats", (), {"dropped_messages": 0, "kept_tokens": 100})()

    monkeypatch.setattr("src.agents.compaction.compact_messages", _fake_compact_messages)
    monkeypatch.setattr("src.agents.compaction.resolve_context_window_tokens", lambda model=None: 64000)

    session = SkillSession(
        skill_name="demo",
        original_user_request="help",
        completed_steps=[
            {"type": "execute", "result": "x" * 9000, "timestamp": 1},
            {"type": "execute", "result": "y" * 9000, "timestamp": 2},
        ],
    )

    await compact_skill_session_async(session, budget_tokens=100)

    assert "max_tokens" in captured
    assert captured["max_tokens"] == 100
    assert "budget_tokens" not in captured
    assert captured["recent_count"] == 3
