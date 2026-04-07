import pytest
import importlib
import asyncio
import copy

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
                snapshot_version="phase3.v1",
                active_skill_session={"skill": "demo"},
                last_execution_id="req-700",
                runtime_state={"active_skill_session": {"skill": "demo"}},
                reconstructed_state={"has_active_skill_session": True},
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
    assert data["snapshot_version"] == "phase3.v1"
    assert data["active_skill_session"] == {"skill": "demo"}
    assert data["last_execution_id"] == "req-700"
    assert data["runtime_state"]["active_skill_session"] == {"skill": "demo"}
    assert data["reconstructed_state"]["has_active_skill_session"] is True
    assert data["metadata"] == {"k": "v"}
    assert data["runtime_events"][0]["event_type"] == "recovery.hydrated"


@pytest.mark.asyncio
async def test_session_manager_pending_delegation_metadata_lifecycle():
    manager = SessionManager(auto_save=False)
    session_id = "session-delegation-metadata"
    await manager.get_session(session_id)

    await manager.add_pending_delegation(
        session_id,
        {"delegation_id": "del-meta-1", "objective": "Test", "status": "pending"},
    )
    session = await manager.get_session(session_id)
    pending = session["metadata"].get("pending_delegations")
    assert isinstance(pending, list)
    assert pending[0]["delegation_id"] == "del-meta-1"

    await manager.complete_pending_delegation(session_id, "del-meta-1", status="completed")
    session_after = await manager.get_session(session_id)
    assert session_after["metadata"].get("pending_delegations") == []
    completed = session_after["metadata"].get("completed_delegations")
    assert isinstance(completed, list)
    assert completed[-1]["delegation_id"] == "del-meta-1"
    assert completed[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_session_manager_metadata_updates_schedule_persistence(monkeypatch):
    manager = SessionManager(auto_save=True)
    manager.persistence_enabled = True
    session_id = "session-persist-metadata"
    await manager.get_session(session_id)

    save_calls = []

    async def _fake_save_session(*, session_id, channel, messages, metadata):
        save_calls.append(
            {
                "session_id": session_id,
                "channel": channel,
                "messages": list(messages),
                "metadata": copy.deepcopy(metadata),
            }
        )

    created_tasks = []

    def _run_now(coro):
        created_tasks.append(coro)
        loop = asyncio.get_running_loop()
        return loop.create_task(coro)

    monkeypatch.setattr("src.sessions.manager.session_persistence.save_session", _fake_save_session)
    monkeypatch.setattr("src.sessions.manager.asyncio.create_task", _run_now)

    await manager.set_last_execution_id(session_id, "req-1")
    await manager.add_pending_delegation(session_id, {"delegation_id": "del-1", "status": "pending"})
    await manager.complete_pending_delegation(session_id, "del-1", status="completed")
    await asyncio.sleep(0)

    assert len(created_tasks) == 3
    assert len(save_calls) == 3
    metadata_states = [call["metadata"] for call in save_calls]
    assert any(state.get("last_execution_id") == "req-1" for state in metadata_states)
    final_session = await manager.get_session(session_id)
    assert final_session["metadata"]["pending_delegations"] == []
    assert final_session["metadata"]["completed_delegations"][-1]["delegation_id"] == "del-1"
