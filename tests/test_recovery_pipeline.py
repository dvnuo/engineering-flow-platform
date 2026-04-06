import pytest

from src.runtime.recovery_pipeline import (
    DefaultRecoveryPipeline,
    RecoveryHydrationResult,
    build_default_recovery_pipeline,
)


@pytest.mark.asyncio
async def test_recovery_snapshot_builds_from_in_memory_session(monkeypatch):
    class _StubSessionManager:
        sessions = {
            "s1": {
                "history": [{"role": "user", "content": "hi"}],
                "metadata": {
                    "active_skill_session": {"skill": "demo"},
                    "last_execution_id": "req-123",
                },
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:01Z",
            }
        }

    monkeypatch.setattr("src.sessions.manager.session_manager", _StubSessionManager)

    pipeline = DefaultRecoveryPipeline()
    snapshot = await pipeline.build_snapshot("s1")

    assert snapshot is not None
    assert snapshot.session_id == "s1"
    assert snapshot.message_count == 1
    assert snapshot.active_skill_session == {"skill": "demo"}
    assert snapshot.last_execution_id == "req-123"
    assert any(evt.get("event_type") == "recovery.snapshot_built" for evt in snapshot.runtime_events)


@pytest.mark.asyncio
async def test_recovery_pipeline_hydrates_from_metadata_fallback(monkeypatch):
    class _StubSessionManager:
        sessions = {}

        @staticmethod
        async def get_session(_session_id):
            return {
                "history": [],
                "metadata": {
                    "active_skill_session": {"skill": "meta"},
                    "last_execution_id": "req-200",
                },
                "created_at": "2026-01-02T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            }

    class _StubPersistence:
        @staticmethod
        async def load_session(_session_id):
            return {
                "messages": [],
                "metadata": {
                    "active_skill_session": {"skill": "meta"},
                    "last_execution_id": "req-200",
                },
            }

    monkeypatch.setattr("src.sessions.manager.session_manager", _StubSessionManager)
    monkeypatch.setattr("src.sessions.persistence.session_persistence", _StubPersistence)

    pipeline = DefaultRecoveryPipeline()
    result = await pipeline.hydrate_session_state("s2")

    assert result.recovered is True
    assert result.active_skill_session == {"skill": "meta"}
    assert result.last_execution_id == "req-200"
    assert "active_skill_session_restored_from_metadata" in result.warnings
    assert any(evt.get("event_type") == "recovery.hydrated" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_recovery_pipeline_handles_missing_session_safely(monkeypatch):
    class _StubSessionManager:
        sessions = {}

    class _StubPersistence:
        @staticmethod
        async def load_session(_session_id):
            return None

    monkeypatch.setattr("src.sessions.manager.session_manager", _StubSessionManager)
    monkeypatch.setattr("src.sessions.persistence.session_persistence", _StubPersistence)

    pipeline = DefaultRecoveryPipeline()
    result = await pipeline.hydrate_session_state("missing")

    assert result.recovered is False
    assert "session_not_found" in result.warnings
    assert any(evt.get("event_type") == "recovery.warning" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_recovery_reconcile_returns_structured_result(monkeypatch):
    class _StubSessionManager:
        sessions = {
            "s3": {
                "history": [],
                "active_skill_session": {"skill": "direct"},
                "metadata": {"last_execution_id": "req-300"},
            }
        }

    class _StubPersistence:
        @staticmethod
        async def load_session(_session_id):
            return None

    monkeypatch.setattr("src.sessions.manager.session_manager", _StubSessionManager)
    monkeypatch.setattr("src.sessions.persistence.session_persistence", _StubPersistence)

    pipeline = DefaultRecoveryPipeline()
    result = await pipeline.reconcile_session_state("s3")

    assert isinstance(result, RecoveryHydrationResult)
    assert result.recovered is True
    assert result.active_skill_session == {"skill": "direct"}
    assert result.last_execution_id == "req-300"
    assert any(evt.get("event_type") == "recovery.reconciled" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_build_default_recovery_pipeline_returns_default_impl():
    pipeline = build_default_recovery_pipeline()
    assert isinstance(pipeline, DefaultRecoveryPipeline)
