"""Lightweight runtime recovery pipeline for session hydration/snapshotting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.runtime.contracts import SessionSnapshot, make_session_snapshot
from src.runtime.events import build_runtime_event


@dataclass
class RecoverySnapshot:
    snapshot_version: str
    session_id: str
    persisted_session: Dict[str, Any] = field(default_factory=dict)
    runtime_state: Dict[str, Any] = field(default_factory=dict)
    reconstructed_state: Dict[str, Any] = field(default_factory=dict)
    # compatibility fields retained for existing callers/tests
    message_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    active_skill_session: Optional[Dict[str, Any]] = None
    last_execution_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    summary_flags: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    runtime_events: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RecoveryHydrationResult:
    session_id: str
    recovered: bool
    snapshot_version: Optional[str] = None
    active_skill_session: Optional[Dict[str, Any]] = None
    last_execution_id: Optional[str] = None
    runtime_state: Dict[str, Any] = field(default_factory=dict)
    reconstructed_state: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    runtime_events: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class RecoveryPipeline:
    async def build_snapshot(self, session_id: str) -> Optional[RecoverySnapshot]:
        return None

    async def hydrate_session_state(self, session_id: str) -> RecoveryHydrationResult:
        return RecoveryHydrationResult(session_id=session_id, recovered=False, warnings=["not_implemented"])

    async def reconcile_session_state(self, session_id: str) -> RecoveryHydrationResult:
        return await self.hydrate_session_state(session_id)


class DefaultRecoveryPipeline(RecoveryPipeline):
    """Incremental recovery pipeline aligned with current session persistence model."""

    async def build_snapshot(self, session_id: str) -> Optional[RecoverySnapshot]:
        if not session_id:
            return None

        session_info = await self._load_session_info(session_id, hydrate_via_manager=False)
        if session_info is None:
            return None
        session = session_info["session"]
        source = session_info["source"]
        return await self._build_snapshot_from_session(session_id=session_id, session=session, source=source)

    async def hydrate_session_state(self, session_id: str) -> RecoveryHydrationResult:
        if not session_id:
            return RecoveryHydrationResult(
                session_id=session_id,
                recovered=False,
                warnings=["missing_session_id"],
                runtime_events=[self._event(session_id, "recovery.warning", "warning", {"warning": "missing_session_id"})],
            )

        warnings: List[str] = []
        runtime_events: List[Dict[str, Any]] = []
        session_info = await self._load_session_info(session_id, hydrate_via_manager=True)
        if session_info is None:
            warnings.append("session_not_found")
            runtime_events.append(
                self._event(
                    session_id,
                    "recovery.warning",
                    "warning",
                    {
                        "warning": "session_not_found",
                        "source": "missing",
                        "message_count": 0,
                        "has_active_skill_session": False,
                        "has_last_execution_id": False,
                        "warning_count": len(warnings),
                    },
                )
            )
            return RecoveryHydrationResult(
                session_id=session_id,
                recovered=False,
                warnings=warnings,
                runtime_events=runtime_events,
                metadata={},
            )

        session_record = session_info["session"]
        snapshot = await self._build_snapshot_from_session(
            session_id=session_id,
            session=session_record,
            source=session_info["source"],
            warnings=warnings,
        )
        metadata = dict(snapshot.metadata)
        active_skill_session = snapshot.active_skill_session
        last_execution_id = snapshot.last_execution_id
        message_count = snapshot.message_count

        runtime_events.append(
            self._event(
                session_id,
                "recovery.hydrated",
                "hydrated",
                {
                    "source": snapshot.reconstructed_state.get("recovery_source"),
                    "message_count": message_count,
                    "has_active_skill_session": active_skill_session is not None,
                    "has_last_execution_id": bool(last_execution_id),
                    "warning_count": len(warnings),
                },
            )
        )

        return RecoveryHydrationResult(
            session_id=session_id,
            recovered=True,
            snapshot_version=snapshot.snapshot_version,
            active_skill_session=active_skill_session,
            last_execution_id=last_execution_id,
            runtime_state=dict(snapshot.runtime_state),
            reconstructed_state=dict(snapshot.reconstructed_state),
            warnings=warnings,
            runtime_events=runtime_events,
            metadata=metadata,
        )

    async def reconcile_session_state(self, session_id: str) -> RecoveryHydrationResult:
        hydration = await self.hydrate_session_state(session_id)
        reconciliation_hint = {
            "has_active_skill_session": hydration.active_skill_session is not None,
            "has_last_execution_id": bool(hydration.last_execution_id),
            "warning_count": len(hydration.warnings),
        }
        hydration.runtime_events.append(
            self._event(
                session_id,
                "recovery.reconciled",
                "reconciled" if hydration.recovered else "warning",
                {
                    **reconciliation_hint,
                    "warning_count": len(hydration.warnings),
                },
            )
        )
        return hydration

    async def _load_session_info(self, session_id: str, *, hydrate_via_manager: bool) -> Optional[Dict[str, Any]]:
        from src.sessions.manager import session_manager
        from src.sessions.persistence import session_persistence

        memory_session = session_manager.sessions.get(session_id)
        if isinstance(memory_session, dict):
            return {"source": "memory", "session": _normalize_session_record(memory_session, source="memory")}

        persisted = await session_persistence.load_session(session_id)
        if not isinstance(persisted, dict):
            return None

        if hydrate_via_manager:
            try:
                hydrated = await session_manager.get_session(session_id)
                if isinstance(hydrated, dict):
                    return {"source": "persistence", "session": _normalize_session_record(hydrated, source="persistence")}
            except Exception:
                pass

        return {"source": "persistence", "session": _normalize_session_record(persisted, source="persistence")}

    def _event(self, session_id: str, event_type: str, state: str, detail_payload: Dict[str, Any]) -> Dict[str, Any]:
        return build_runtime_event(
            event_type=event_type,
            execution_type="recovery",
            state=state,
            session_id=session_id,
            request_id=None,
            agent_id=None,
            summary=event_type,
            detail_payload=detail_payload,
            legacy_payload={"legacy_type": event_type.replace(".", "_")},
        )

    async def _build_snapshot_from_session(
        self,
        *,
        session_id: str,
        session: Dict[str, Any],
        source: str,
        warnings: Optional[List[str]] = None,
    ) -> RecoverySnapshot:
        runtime_warnings = warnings if warnings is not None else []
        metadata = _metadata_from_session(session)
        active_skill_session = _extract_active_skill_session(session)
        if session.get("active_skill_session") is None and metadata.get("active_skill_session") is not None:
            runtime_warnings.append("active_skill_session_restored_from_metadata")
        last_execution_id = _extract_last_execution_id(session)
        message_count = len(session.get("history") or [])
        pending_tool_tasks = await self._safe_pending_tool_tasks(session_id, runtime_warnings)
        active_subagents = await self._safe_active_subagents(session_id, runtime_warnings)
        pending_delegations = metadata.get("pending_delegations") if isinstance(metadata.get("pending_delegations"), list) else []
        runtime_state = {
            "active_skill_session": active_skill_session,
            "last_execution_id": last_execution_id,
            "pending_tool_tasks": pending_tool_tasks,
            "active_subagents": active_subagents,
            "pending_delegations": list(pending_delegations),
        }
        reconstructed_state = {
            "has_history": bool(session.get("history")),
            "has_active_skill_session": active_skill_session is not None,
            "has_last_execution_id": bool(last_execution_id),
            "has_pending_tool_tasks": bool(pending_tool_tasks),
            "has_active_subagents": bool(active_subagents),
            "has_pending_delegations": bool(pending_delegations),
            "has_shared_context_ref": _has_shared_context_ref(metadata),
            "has_materialized_context_ref": _has_materialized_context_ref(metadata),
            "recovery_source": source,
        }
        persisted_session = {
            "history": list(session.get("history") or []),
            "messages": list(session.get("history") or []),
            "metadata": metadata,
            "created_at": _safe_string(session.get("created_at")),
            "updated_at": _safe_string(session.get("updated_at")),
        }
        contract_snapshot: SessionSnapshot = make_session_snapshot(
            snapshot_version="phase3.v1",
            session_id=session_id,
            persisted_session=persisted_session,
            runtime_state=runtime_state,
            reconstructed_state=reconstructed_state,
            created_at=persisted_session.get("created_at"),
            updated_at=persisted_session.get("updated_at"),
        )
        return RecoverySnapshot(
            snapshot_version=contract_snapshot.snapshot_version,
            session_id=contract_snapshot.session_id,
            persisted_session=contract_snapshot.persisted_session,
            runtime_state=contract_snapshot.runtime_state,
            reconstructed_state=contract_snapshot.reconstructed_state,
            message_count=message_count,
            metadata=metadata,
            active_skill_session=active_skill_session,
            last_execution_id=last_execution_id,
            created_at=contract_snapshot.created_at,
            updated_at=contract_snapshot.updated_at,
            summary_flags={
                "has_history": reconstructed_state["has_history"],
                "has_active_skill_session": reconstructed_state["has_active_skill_session"],
                "has_last_execution_id": reconstructed_state["has_last_execution_id"],
                "source": source,
            },
            warnings=list(runtime_warnings),
            runtime_events=[
                self._event(
                    session_id,
                    "recovery.snapshot_built",
                    "snapshot_built",
                    {
                        "source": source,
                        "message_count": message_count,
                        "has_active_skill_session": active_skill_session is not None,
                        "has_last_execution_id": bool(last_execution_id),
                        "has_pending_tool_tasks": bool(pending_tool_tasks),
                        "has_active_subagents": bool(active_subagents),
                        "has_pending_delegations": bool(pending_delegations),
                        "warning_count": len(runtime_warnings),
                    },
                )
            ],
        )

    async def _safe_pending_tool_tasks(self, session_id: str, warnings: List[str]) -> List[Dict[str, Any]]:
        try:
            from src.agents.tasks import task_manager

            return task_manager.list_task_summaries(session_id=session_id)
        except Exception:
            warnings.append("pending_tool_tasks_unavailable")
            return []

    async def _safe_active_subagents(self, session_id: str, warnings: List[str]) -> List[Dict[str, Any]]:
        try:
            from src.agents.subagent import list_active_subagent_summaries

            return list_active_subagent_summaries(parent_session_id=session_id)
        except Exception:
            warnings.append("active_subagents_unavailable")
            return []


def _metadata_from_session(session: Dict[str, Any]) -> Dict[str, Any]:
    metadata = session.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _extract_active_skill_session(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    active = session.get("active_skill_session")
    if isinstance(active, dict):
        return dict(active)

    metadata = _metadata_from_session(session)
    metadata_active = metadata.get("active_skill_session")
    if isinstance(metadata_active, dict):
        return dict(metadata_active)
    return None


def _extract_last_execution_id(session: Dict[str, Any]) -> Optional[str]:
    metadata = _metadata_from_session(session)
    last_execution_id = metadata.get("last_execution_id")
    if isinstance(last_execution_id, str) and last_execution_id.strip():
        return last_execution_id.strip()
    return None


def _has_shared_context_ref(metadata: Dict[str, Any]) -> bool:
    if not isinstance(metadata, dict):
        return False
    value = metadata.get("shared_context_ref")
    if isinstance(value, str) and value.strip():
        return True
    pending = metadata.get("pending_delegations")
    if isinstance(pending, list):
        for item in pending:
            if isinstance(item, dict):
                ref = item.get("shared_context_ref")
                if isinstance(ref, str) and ref.strip():
                    return True
    return False


def _has_materialized_context_ref(metadata: Dict[str, Any]) -> bool:
    if not isinstance(metadata, dict):
        return False
    if metadata.get("shared_context_materialized") is True:
        return True
    pending = metadata.get("pending_delegations")
    if isinstance(pending, list):
        for item in pending:
            if not isinstance(item, dict):
                continue
            if item.get("shared_context_materialized") is True:
                return True
            context_ref = item.get("context_ref")
            if isinstance(context_ref, dict) and bool(context_ref):
                return True
    return False


def _safe_string(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value
    return None


def build_default_recovery_pipeline() -> RecoveryPipeline:
    return DefaultRecoveryPipeline()


def _normalize_session_record(session_record: Dict[str, Any], *, source: str) -> Dict[str, Any]:
    metadata = session_record.get("metadata")
    normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    history = session_record.get("history")
    if history is None:
        history = session_record.get("messages")
    normalized_history = list(history) if isinstance(history, list) else []

    return {
        "history": normalized_history,
        "metadata": normalized_metadata,
        "active_skill_session": session_record.get("active_skill_session"),
        "created_at": session_record.get("created_at"),
        "updated_at": session_record.get("updated_at"),
        "_recovery_source": source,
    }


_default_recovery_pipeline = build_default_recovery_pipeline()


def get_recovery_pipeline() -> RecoveryPipeline:
    return _default_recovery_pipeline


recovery_pipeline = _default_recovery_pipeline
