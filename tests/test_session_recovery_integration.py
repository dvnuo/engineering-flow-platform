import pytest
import importlib
from pathlib import Path

from src.runtime.recovery_pipeline import RecoveryHydrationResult
from src.efp_runtime.session.file_store import FileSessionStore
from src.efp_runtime.session.gateway_facade import RuntimeSessionManager, runtime_session_manager


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_legacy_session_manager_module_is_removed_and_runtime_facade_backs_gateway(tmp_path):
    assert not (ROOT / "src/sessions/manager.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.sessions.manager")

    manager = RuntimeSessionManager(root=tmp_path, auto_save=False)
    assert isinstance(manager.store, FileSessionStore)

    await manager.add_message("wrapper-session", "user", "hello wrapper")
    assert manager.store.read_history("wrapper-session")[0].parts[0].text == "hello wrapper"
    assert isinstance(runtime_session_manager.store, FileSessionStore)


@pytest.mark.asyncio
async def test_runtime_v2_session_facade_recover_session_state_calls_runtime_pipeline(monkeypatch, tmp_path):
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

    manager = RuntimeSessionManager(root=tmp_path, auto_save=False)
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
async def test_runtime_v2_session_facade_pending_delegation_metadata_lifecycle(tmp_path):
    manager = RuntimeSessionManager(root=tmp_path, auto_save=False)
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
async def test_runtime_v2_session_facade_metadata_updates_file_store(tmp_path):
    manager = RuntimeSessionManager(root=tmp_path, auto_save=True)
    session_id = "session-persist-metadata"
    await manager.get_session(session_id)

    await manager.set_last_execution_id(session_id, "req-1")
    await manager.add_pending_delegation(session_id, {"delegation_id": "del-1", "status": "pending"})
    await manager.complete_pending_delegation(session_id, "del-1", status="completed")

    final_session = RuntimeSessionManager(root=tmp_path).store.get_session(session_id)
    assert final_session.metadata["last_execution_id"] == "req-1"
    assert final_session.metadata["pending_delegations"] == []
    assert final_session.metadata["completed_delegations"][-1]["delegation_id"] == "del-1"

    final_session_view = await manager.get_session(session_id)
    assert final_session_view["metadata"]["last_execution_id"] == "req-1"
    assert final_session_view["metadata"]["pending_delegations"] == []
