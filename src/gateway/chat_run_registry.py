"""In-memory registry for resumable chat executions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


TERMINAL_STATES = {"completed", "failed", "cancelled"}
RETAINED_EVENT_LIST_KEYS = ("events", "runtime_events", "thinking_events")
RETAINED_EVENT_TAIL_ITEMS = 100
DEFAULT_STALE_RUNNING_SECONDS = 6 * 3600


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def compact_final_payload(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    """Drop full event streams from a retained terminal payload.

    Live viewers already received the complete stream over SSE. The registry
    only serves late reconnects, and the chat UI merges at most the last 100
    events from a final payload, so retaining the full per-delta event list
    for up to ``max_records`` runs only accumulates memory.
    """
    compact = dict(payload or {})
    for key in RETAINED_EVENT_LIST_KEYS:
        value = compact.get(key)
        if isinstance(value, list) and len(value) > RETAINED_EVENT_TAIL_ITEMS:
            compact[key] = value[-RETAINED_EVENT_TAIL_ITEMS:]
            compact[f"{key}_count"] = len(value)
            compact[f"{key}_truncated"] = True
    return compact


@dataclass
class ChatRunRecord:
    request_id: str
    session_id: str
    engine: str = "native"
    state: str = "running"
    started_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    latest_event_at: str = ""
    latest_event_seq: int = 0
    replay_available: bool = True
    detached_viewers: int = 0
    final_payload: Optional[Dict[str, Any]] = None
    error_payload: Optional[Dict[str, Any]] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_payload(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "engine": self.engine,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "state": self.state,
            "terminal": self.terminal,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "latest_event_at": self.latest_event_at,
            "latest_event_seq": self.latest_event_seq,
            "replay_available": self.replay_available,
            "detached_viewers": self.detached_viewers,
            "final_payload": self.final_payload,
            "error_payload": self.error_payload,
        }


class ChatRunRegistry:
    def __init__(
        self,
        *,
        max_records: int = 512,
        stale_running_seconds: float = DEFAULT_STALE_RUNNING_SECONDS,
    ) -> None:
        self._records: Dict[str, ChatRunRecord] = {}
        self._max_records = max_records
        self._stale_running_seconds = max(0.0, float(stale_running_seconds))

    def start(self, *, session_id: str, request_id: str, engine: str = "native") -> ChatRunRecord:
        existing = self.get(request_id, session_id=session_id)
        if existing is not None:
            return existing
        record = ChatRunRecord(request_id=request_id, session_id=session_id, engine=engine)
        self._records[request_id] = record
        self._fail_stale_running_records()
        self._prune_terminal_records()
        return record

    def _fail_stale_running_records(self) -> None:
        """Mark long-inactive non-terminal records failed so they become prunable.

        A record left in ``running`` this long means the producing run died
        before ``complete()``/``fail()`` (for example the request handler was
        cancelled by a client disconnect); otherwise ``record_event`` would
        have refreshed ``updated_at``.
        """
        if self._stale_running_seconds <= 0:
            return
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=self._stale_running_seconds)
        ).isoformat().replace("+00:00", "Z")
        for record in self._records.values():
            if record.terminal or record.updated_at >= cutoff:
                continue
            record.state = "failed"
            record.error_payload = {
                "error": "chat_run_stale",
                "detail": "No run activity before the stale timeout; a terminal outcome was never recorded.",
                "session_id": record.session_id,
                "request_id": record.request_id,
            }
            record.updated_at = utc_now_iso()
            record.task = None

    def _prune_terminal_records(self) -> None:
        if len(self._records) <= self._max_records:
            return
        overflow = len(self._records) - self._max_records
        terminal_records = sorted(
            (record for record in self._records.values() if record.terminal),
            key=lambda record: record.updated_at,
        )
        for record in terminal_records[:overflow]:
            self._records.pop(record.request_id, None)

    def get(self, request_id: str, *, session_id: str | None = None) -> ChatRunRecord | None:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return None
        record = self._records.get(normalized_request_id)
        if record is None:
            return None
        if session_id and record.session_id != session_id:
            return None
        return record

    def attach_task(self, request_id: str, task: asyncio.Task) -> None:
        record = self.get(request_id)
        if record is not None:
            record.task = task
            record.updated_at = utc_now_iso()

    def record_event(self, request_id: str, event: Dict[str, Any]) -> None:
        record = self.get(request_id)
        if record is None or record.terminal:
            return
        record.latest_event_seq += 1
        created_at = event.get("created_at") if isinstance(event, dict) else None
        record.latest_event_at = str(created_at or utc_now_iso())
        record.updated_at = utc_now_iso()

    def mark_detached(self, request_id: str) -> None:
        record = self.get(request_id)
        if record is None or record.terminal:
            return
        record.detached_viewers += 1
        record.updated_at = utc_now_iso()

    def complete(self, request_id: str, final_payload: Dict[str, Any]) -> None:
        record = self.get(request_id)
        if record is None:
            return
        record.state = "completed"
        record.final_payload = compact_final_payload(final_payload)
        record.updated_at = utc_now_iso()
        record.task = None

    def fail(self, request_id: str, error_payload: Dict[str, Any]) -> None:
        record = self.get(request_id)
        if record is None:
            return
        if record.state == "cancelled":
            return
        record.state = "failed"
        record.error_payload = compact_final_payload(error_payload)
        record.updated_at = utc_now_iso()
        record.task = None

    def cancel(self, request_id: str) -> bool:
        record = self.get(request_id)
        if record is None or record.terminal:
            return False
        record.state = "cancelled"
        record.updated_at = utc_now_iso()
        task = record.task
        record.task = None
        if task is not None and not task.done():
            task.cancel()
        return True


chat_run_registry = ChatRunRegistry()
