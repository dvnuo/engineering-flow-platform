import pytest
import importlib

from src.runtime.recovery_pipeline import RecoveryHydrationResult
from src.sessions.manager import SessionManager


@pytest.mark.asyncio
async def test_session_manager_recover_session_state_calls_runtime_pipeline(monkeypatch):
    calls = []

    class _StubPipeline:
        async def hydrate_session_state(self, session_id):
            calls.append(session_id)
            return RecoveryHydrationResult(
                session_id=session_id,
                recovered=True,
                active_skill_session={"skill": "demo"},
                last_execution_id="req-700",
                warnings=[],
                runtime_events=[{"event_type": "recovery.hydrated"}],
                metadata={"k": "v"},
            )

    recovery_pipeline_module = importlib.import_module("src.runtime.recovery_pipeline")
    monkeypatch.setattr(recovery_pipeline_module, "get_recovery_pipeline", lambda: _StubPipeline())

    manager = SessionManager(auto_save=False)
    data = await manager.recover_session_state("session-700")

    assert calls == ["session-700"]
    assert data["recovered"] is True
    assert data["active_skill_session"] == {"skill": "demo"}
    assert data["last_execution_id"] == "req-700"
    assert data["metadata"] == {"k": "v"}
    assert data["runtime_events"][0]["event_type"] == "recovery.hydrated"
