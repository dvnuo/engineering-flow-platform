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
    assert snapshot.snapshot_version == "phase3.v1"
    assert snapshot.session_id == "s1"
    assert snapshot.message_count == 1
    assert snapshot.active_skill_session == {"skill": "demo"}
    assert snapshot.last_execution_id == "req-123"
    assert snapshot.persisted_session["history"][0]["content"] == "hi"
    assert snapshot.runtime_state["active_skill_session"] == {"skill": "demo"}
    assert snapshot.reconstructed_state["has_active_skill_session"] is True
    assert any(evt.get("event_type") == "recovery.snapshot_built" for evt in snapshot.runtime_events)
    assert snapshot.summary_flags["source"] == "memory"


@pytest.mark.asyncio
async def test_recovery_snapshot_falls_back_to_persistence(monkeypatch):
    class _StubSessionManager:
        sessions = {}

    class _StubPersistence:
        @staticmethod
        async def load_session(_session_id):
            return {
                "messages": [{"role": "user", "content": "persisted"}],
                "metadata": {
                    "active_skill_session": {"skill": "persisted-skill"},
                    "last_execution_id": "req-persisted",
                },
                "created_at": "2026-01-05T00:00:00Z",
                "updated_at": "2026-01-05T00:05:00Z",
            }

    monkeypatch.setattr("src.sessions.manager.session_manager", _StubSessionManager)
    monkeypatch.setattr("src.sessions.persistence.session_persistence", _StubPersistence)

    pipeline = DefaultRecoveryPipeline()
    snapshot = await pipeline.build_snapshot("persisted-session")

    assert snapshot is not None
    assert snapshot.message_count == 1
    assert snapshot.active_skill_session == {"skill": "persisted-skill"}
    assert snapshot.last_execution_id == "req-persisted"
    assert snapshot.summary_flags["source"] == "persistence"
    assert any(evt.get("event_type") == "recovery.snapshot_built" for evt in snapshot.runtime_events)


@pytest.mark.asyncio
async def test_recovery_snapshot_includes_task_and_subagent_summaries(monkeypatch):
    class _StubSessionManager:
        sessions = {
            "s-recovery": {
                "history": [{"role": "user", "content": "hi"}],
                "metadata": {"pending_delegations": [{"delegation_id": "d1"}]},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:01Z",
            }
        }

    monkeypatch.setattr("src.sessions.manager.session_manager", _StubSessionManager)
    monkeypatch.setattr(
        "src.agents.tasks.task_manager.list_task_summaries",
        lambda session_id=None: [{"task_id": "t1", "session_id": "s-recovery", "tool_name": "run", "status": "running", "created_at": "now", "finished_at": None}],
    )
    monkeypatch.setattr(
        "src.agents.subagent.list_active_subagent_summaries",
        lambda parent_session_id=None: [
            {
                "session_key": "sa1",
                "task": "x",
                "status": "running",
                "model": "gpt",
                "thinking": "low",
                "created_at": "now",
                "parent_session_id": parent_session_id,
            }
        ],
    )

    snapshot = await DefaultRecoveryPipeline().build_snapshot("s-recovery")
    assert snapshot is not None
    assert snapshot.runtime_state["pending_tool_tasks"][0]["task_id"] == "t1"
    assert snapshot.runtime_state["active_subagents"][0]["session_key"] == "sa1"
    assert snapshot.runtime_state["pending_delegations"][0]["delegation_id"] == "d1"
    assert snapshot.reconstructed_state["has_pending_tool_tasks"] is True
    assert snapshot.reconstructed_state["has_active_subagents"] is True
    assert snapshot.reconstructed_state["has_pending_delegations"] is True


@pytest.mark.asyncio
async def test_recovery_snapshot_registry_failures_are_soft(monkeypatch):
    class _StubSessionManager:
        sessions = {"s-soft": {"history": [], "metadata": {}}}

    monkeypatch.setattr("src.sessions.manager.session_manager", _StubSessionManager)
    monkeypatch.setattr(
        "src.agents.tasks.task_manager.list_task_summaries",
        lambda session_id=None: (_ for _ in ()).throw(RuntimeError("task registry down")),
    )
    monkeypatch.setattr(
        "src.agents.subagent.list_active_subagent_summaries",
        lambda parent_session_id=None: (_ for _ in ()).throw(RuntimeError("subagent registry down")),
    )

    snapshot = await DefaultRecoveryPipeline().build_snapshot("s-soft")
    assert snapshot is not None
    assert snapshot.runtime_state["pending_tool_tasks"] == []
    assert snapshot.runtime_state["active_subagents"] == []
    assert "pending_tool_tasks_unavailable" in snapshot.warnings
    assert "active_subagents_unavailable" in snapshot.warnings


@pytest.mark.asyncio
async def test_recovery_snapshot_subagents_are_scoped_by_parent_session(monkeypatch):
    class _StubSessionManager:
        sessions = {"s-scope": {"history": [], "metadata": {}}}

    captured = {}

    def _fake_subagent_summaries(parent_session_id=None):
        captured["parent_session_id"] = parent_session_id
        return [{"session_key": "only-this", "status": "started", "parent_session_id": parent_session_id}]

    monkeypatch.setattr("src.sessions.manager.session_manager", _StubSessionManager)
    monkeypatch.setattr("src.agents.tasks.task_manager.list_task_summaries", lambda session_id=None: [])
    monkeypatch.setattr("src.agents.subagent.list_active_subagent_summaries", _fake_subagent_summaries)

    snapshot = await DefaultRecoveryPipeline().build_snapshot("s-scope")
    assert snapshot is not None
    assert captured["parent_session_id"] == "s-scope"
    assert snapshot.runtime_state["active_subagents"][0]["parent_session_id"] == "s-scope"


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
    assert result.snapshot_version == "phase3.v1"
    assert result.active_skill_session == {"skill": "meta"}
    assert result.last_execution_id == "req-200"
    assert "runtime_state" in result.__dict__
    assert "reconstructed_state" in result.__dict__
    assert "active_skill_session_restored_from_metadata" in result.warnings
    assert any(evt.get("event_type") == "recovery.hydrated" for evt in result.runtime_events)
    hydrated_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "recovery.hydrated")
    assert hydrated_event["detail_payload"]["source"] == "persistence"
    assert hydrated_event["detail_payload"]["message_count"] == 0


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
async def test_recovery_reconcile_after_persisted_fallback_has_reconciled_event(monkeypatch):
    class _StubSessionManager:
        sessions = {}

        @staticmethod
        async def get_session(_session_id):
            return {
                "history": [],
                "metadata": {"last_execution_id": "req-500"},
                "active_skill_session": {"skill": "persisted"},
            }

    class _StubPersistence:
        @staticmethod
        async def load_session(_session_id):
            return {
                "messages": [],
                "metadata": {"last_execution_id": "req-500", "active_skill_session": {"skill": "persisted"}},
            }

    monkeypatch.setattr("src.sessions.manager.session_manager", _StubSessionManager)
    monkeypatch.setattr("src.sessions.persistence.session_persistence", _StubPersistence)

    pipeline = DefaultRecoveryPipeline()
    result = await pipeline.reconcile_session_state("persisted-reconcile")

    assert result.recovered is True
    assert any(evt.get("event_type") == "recovery.reconciled" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_build_default_recovery_pipeline_returns_default_impl():
    pipeline = build_default_recovery_pipeline()
    assert isinstance(pipeline, DefaultRecoveryPipeline)
