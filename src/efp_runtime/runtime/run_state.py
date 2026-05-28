"""Session-scoped run state for Runtime v2."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Dict, Optional

from ..types import new_id, utc_now_iso


class RunStatus:
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    ERROR = "error"


class SessionBusyError(RuntimeError):
    """Raised when a session already has an active run."""

    def __init__(self, session_id: str, run_id: str):
        self.session_id = session_id
        self.run_id = run_id
        super().__init__(f"Session {session_id!r} already has active run {run_id!r}.")


@dataclass
class RuntimeRunRecord:
    session_id: str
    run_id: str
    status: str = RunStatus.RUNNING
    active: bool = True
    cancel_requested: bool = False
    started_at: str = ""
    finished_at: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = utc_now_iso()


class RuntimeRunState:
    """Track one active run per session and cooperative cancellation."""

    def __init__(self) -> None:
        self._runs: Dict[str, RuntimeRunRecord] = {}
        self._lock = RLock()

    def begin(self, session_id: str) -> str:
        if not session_id:
            raise ValueError("session_id is required")
        with self._lock:
            existing = self._runs.get(session_id)
            if existing is not None and existing.active:
                raise SessionBusyError(session_id, existing.run_id)
            run_id = new_id("run")
            self._runs[session_id] = RuntimeRunRecord(
                session_id=session_id,
                run_id=run_id,
            )
            return run_id

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            record = self._runs.get(session_id)
            if record is None or not record.active:
                return False
            record.cancel_requested = True
            record.status = RunStatus.CANCELLING
            return True

    def is_cancelled(self, session_id: str) -> bool:
        with self._lock:
            record = self._runs.get(session_id)
            return bool(record is not None and record.active and record.cancel_requested)

    def finish(self, session_id: str, status: str) -> Optional[RuntimeRunRecord]:
        with self._lock:
            record = self._runs.get(session_id)
            if record is None:
                return None
            record.status = status
            record.active = False
            record.cancel_requested = status == RunStatus.CANCELLED
            record.finished_at = utc_now_iso()
            return replace(record)

    def current(self, session_id: str) -> Optional[RuntimeRunRecord]:
        with self._lock:
            record = self._runs.get(session_id)
            return replace(record) if record is not None else None


__all__ = [
    "RunStatus",
    "RuntimeRunRecord",
    "RuntimeRunState",
    "SessionBusyError",
]
