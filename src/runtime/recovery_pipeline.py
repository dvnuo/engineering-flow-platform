"""Lightweight runtime recovery pipeline for session hydration/snapshotting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.runtime.events import build_runtime_event


@dataclass
class RecoverySnapshot:
    session_id: str
    message_count: int
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
    active_skill_session: Optional[Dict[str, Any]] = None
    last_execution_id: Optional[str] = None
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

        from src.sessions.manager import session_manager

        session = session_manager.sessions.get(session_id)
        if not isinstance(session, dict):
            return None

        metadata = _metadata_from_session(session)
        active_skill_session = _extract_active_skill_session(session)
        last_execution_id = _extract_last_execution_id(session)

        snapshot = RecoverySnapshot(
            session_id=session_id,
            message_count=len(session.get("history") or []),
            metadata=metadata,
            active_skill_session=active_skill_session,
            last_execution_id=last_execution_id,
            created_at=_safe_string(session.get("created_at")),
            updated_at=_safe_string(session.get("updated_at")),
            summary_flags={
                "has_history": bool(session.get("history")),
                "has_active_skill_session": active_skill_session is not None,
                "has_last_execution_id": bool(last_execution_id),
            },
            runtime_events=[
                self._event(
                    session_id,
                    "recovery.snapshot_built",
                    "snapshot_built",
                    {
                        "message_count": len(session.get("history") or []),
                        "has_active_skill_session": active_skill_session is not None,
                        "has_last_execution_id": bool(last_execution_id),
                    },
                )
            ],
        )
        return snapshot

    async def hydrate_session_state(self, session_id: str) -> RecoveryHydrationResult:
        if not session_id:
            return RecoveryHydrationResult(
                session_id=session_id,
                recovered=False,
                warnings=["missing_session_id"],
                runtime_events=[self._event(session_id, "recovery.warning", "warning", {"warning": "missing_session_id"})],
            )

        from src.sessions.manager import session_manager
        from src.sessions.persistence import session_persistence

        warnings: List[str] = []
        runtime_events: List[Dict[str, Any]] = []

        session_record = session_manager.sessions.get(session_id)
        source = "memory"

        if not isinstance(session_record, dict):
            persisted = await session_persistence.load_session(session_id)
            if persisted is None:
                warnings.append("session_not_found")
                runtime_events.append(self._event(session_id, "recovery.warning", "warning", {"warning": "session_not_found"}))
                return RecoveryHydrationResult(
                    session_id=session_id,
                    recovered=False,
                    warnings=warnings,
                    runtime_events=runtime_events,
                    metadata={},
                )
            source = "persistence"
            # Reuse existing manager restoration path where possible.
            try:
                session_record = await session_manager.get_session(session_id)
            except Exception:
                session_record = {
                    "history": persisted.get("messages", []),
                    "metadata": persisted.get("metadata", {}),
                    "active_skill_session": persisted.get("active_skill_session"),
                    "created_at": persisted.get("created_at"),
                    "updated_at": persisted.get("updated_at"),
                }

        metadata = _metadata_from_session(session_record)
        active_skill_session = _extract_active_skill_session(session_record)
        if session_record.get("active_skill_session") is None and metadata.get("active_skill_session") is not None:
            warnings.append("active_skill_session_restored_from_metadata")

        last_execution_id = _extract_last_execution_id(session_record)

        runtime_events.append(
            self._event(
                session_id,
                "recovery.hydrated",
                "hydrated",
                {
                    "source": source,
                    "has_active_skill_session": active_skill_session is not None,
                    "has_last_execution_id": bool(last_execution_id),
                    "warning_count": len(warnings),
                },
            )
        )

        return RecoveryHydrationResult(
            session_id=session_id,
            recovered=True,
            active_skill_session=active_skill_session,
            last_execution_id=last_execution_id,
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
                reconciliation_hint,
            )
        )
        return hydration

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


def _safe_string(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value
    return None


def build_default_recovery_pipeline() -> RecoveryPipeline:
    return DefaultRecoveryPipeline()
