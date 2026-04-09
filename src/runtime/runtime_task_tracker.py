"""In-memory tracker for async runtime task execution state."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


_TASK_TERMINAL_STATUSES = {"success", "error", "blocked"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class RuntimeTaskRecord:
    task_id: str
    request_id: str
    task_type: str
    source: Optional[str]
    session_id: Optional[str]
    agent_id: Optional[str]
    status: str
    accepted_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    trace_id: Optional[str]
    portal_dispatch_id: Optional[str]
    portal_task_id: Optional[str]
    payload: Dict[str, Any]
    error_message: Optional[str]
    background_task: Optional[asyncio.Task[Any]]


class RuntimeTaskTracker:
    """Small in-memory tracker for internal runtime task polling."""

    def __init__(self, *, max_records: int = 512):
        self._records: "OrderedDict[str, RuntimeTaskRecord]" = OrderedDict()
        self._max_records = max(1, int(max_records))

    def create_pending(
        self,
        *,
        task_id: str,
        request_id: str,
        task_type: str,
        source: Optional[str],
        session_id: Optional[str],
        agent_id: Optional[str],
        trace_id: Optional[str],
        portal_dispatch_id: Optional[str],
        portal_task_id: Optional[str],
    ) -> RuntimeTaskRecord:
        record = RuntimeTaskRecord(
            task_id=task_id,
            request_id=request_id,
            task_type=task_type,
            source=source,
            session_id=session_id,
            agent_id=agent_id,
            status="accepted",
            accepted_at=_utc_now_iso(),
            started_at=None,
            finished_at=None,
            trace_id=trace_id,
            portal_dispatch_id=portal_dispatch_id,
            portal_task_id=portal_task_id,
            payload={},
            error_message=None,
            background_task=None,
        )
        self._records[task_id] = record
        self._records.move_to_end(task_id)
        self.prune()
        return record

    def set_background_task(self, task_id: str, background_task: asyncio.Task[Any]) -> None:
        record = self._records.get(task_id)
        if record is None:
            return
        record.background_task = background_task

    def mark_running(self, task_id: str) -> Optional[RuntimeTaskRecord]:
        record = self._records.get(task_id)
        if record is None:
            return None
        record.status = "running"
        if not record.started_at:
            record.started_at = _utc_now_iso()
        return record

    def mark_terminal(self, task_id: str, *, status: str, payload: Dict[str, Any], error_message: Optional[str] = None) -> Optional[RuntimeTaskRecord]:
        record = self._records.get(task_id)
        if record is None:
            return None
        if status not in _TASK_TERMINAL_STATUSES:
            status = "error"
        record.status = status
        record.payload = dict(payload)
        record.error_message = error_message
        record.finished_at = _utc_now_iso()
        if not record.started_at:
            record.started_at = record.finished_at
        self.prune()
        return record

    def mark_internal_failure(self, task_id: str, *, payload: Dict[str, Any], error_message: Optional[str]) -> Optional[RuntimeTaskRecord]:
        return self.mark_terminal(task_id, status="error", payload=payload, error_message=error_message)

    def get(self, task_id: str) -> Optional[RuntimeTaskRecord]:
        return self._records.get(task_id)

    def prune(self) -> None:
        while len(self._records) > self._max_records:
            oldest_task_id, oldest = next(iter(self._records.items()))
            if oldest.status in _TASK_TERMINAL_STATUSES:
                self._records.pop(oldest_task_id, None)
                continue
            break

    def reset(self) -> None:
        self._records.clear()
