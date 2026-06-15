"""Tracker for async runtime task execution state."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_TASK_TERMINAL_STATUSES = {"success", "error", "blocked", "cancelled", "stale"}


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
    context_ref: Optional[Dict[str, Any]] = None
    merged_input_payload: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    trace_headers: Optional[Dict[str, Optional[str]]] = None
    resume_count: int = 0
    last_resumed_at: Optional[str] = None
    admission_id: Optional[str] = None
    admitted_at: Optional[str] = None
    input_hash: Optional[str] = None
    task_session_id: Optional[str] = None
    active_attempt_id: Optional[str] = None
    attempt_count: int = 0
    last_attempt_started_at: Optional[str] = None
    last_progress_at: Optional[str] = None
    pending_permission_request: Optional[Dict[str, Any]] = None
    pending_question_request: Optional[Dict[str, Any]] = None
    completion_source: Optional[str] = None
    cancel_requested: bool = False
    background_task: Optional[asyncio.Task[Any]] = None


class RuntimeTaskTracker:
    """Small tracker for internal runtime task polling.

    Persistence is optional so unit tests and direct imports can continue to use
    the in-memory behavior, while the runtime server can opt in during startup.
    """

    def __init__(self, *, max_records: int = 512, storage_dir: Optional[str | Path] = None):
        self._records: "OrderedDict[str, RuntimeTaskRecord]" = OrderedDict()
        self._max_records = max(1, int(max_records))
        self._storage_dir: Optional[Path] = None
        self.configure_storage(storage_dir)

    @property
    def storage_dir(self) -> Optional[Path]:
        return self._storage_dir

    def configure_storage(self, storage_dir: Optional[str | Path]) -> None:
        if storage_dir is None:
            self._storage_dir = None
            return
        self._storage_dir = Path(storage_dir).expanduser()

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
        context_ref: Optional[Dict[str, Any]] = None,
        merged_input_payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace_headers: Optional[Dict[str, Optional[str]]] = None,
        task_session_id: Optional[str] = None,
    ) -> RuntimeTaskRecord:
        safe_context_ref = _optional_json_dict(context_ref)
        safe_merged_input_payload = _optional_json_dict(merged_input_payload)
        safe_metadata = _optional_json_dict(metadata)
        input_hash = build_task_input_hash(
            task_type=task_type,
            source=source,
            session_id=session_id,
            task_session_id=task_session_id,
            context_ref=safe_context_ref,
            merged_input_payload=safe_merged_input_payload,
            metadata=safe_metadata,
        )
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
            context_ref=safe_context_ref,
            merged_input_payload=safe_merged_input_payload,
            metadata=safe_metadata,
            trace_headers=_optional_json_dict(trace_headers),
            admission_id=_admission_id(task_id=task_id, input_hash=input_hash),
            admitted_at=_utc_now_iso(),
            input_hash=input_hash,
            task_session_id=task_session_id,
            background_task=None,
        )
        self._records[task_id] = record
        self._records.move_to_end(task_id)
        self._persist_record(record)
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
        if record.status in _TASK_TERMINAL_STATUSES:
            return record
        now = _utc_now_iso()
        record.status = "running"
        if not record.started_at:
            record.started_at = now
        record.attempt_count += 1
        record.last_attempt_started_at = now
        record.active_attempt_id = _attempt_id(
            task_id=record.task_id,
            attempt_count=record.attempt_count,
            started_at=now,
        )
        self._persist_record(record)
        return record

    def mark_terminal(
        self,
        task_id: str,
        *,
        status: str,
        payload: Dict[str, Any],
        error_message: Optional[str] = None,
        attempt_id: Optional[str] = None,
        completion_source: Optional[str] = None,
    ) -> Optional[RuntimeTaskRecord]:
        record = self._records.get(task_id)
        if record is None:
            return None
        if status not in _TASK_TERMINAL_STATUSES:
            status = "error"
        if record.status == "cancelled" and status != "cancelled":
            return record
        if attempt_id is not None and record.active_attempt_id != attempt_id:
            return None
        record.status = status
        record.payload = dict(payload)
        record.error_message = error_message
        record.finished_at = _utc_now_iso()
        if not record.started_at:
            record.started_at = record.finished_at
        record.active_attempt_id = None
        if completion_source:
            record.completion_source = completion_source
        _apply_observation_from_payload(record, payload)
        self._persist_record(record)
        self.prune()
        return record

    def mark_internal_failure(
        self,
        task_id: str,
        *,
        payload: Dict[str, Any],
        error_message: Optional[str],
        attempt_id: Optional[str] = None,
    ) -> Optional[RuntimeTaskRecord]:
        return self.mark_terminal(
            task_id,
            status="error",
            payload=payload,
            error_message=error_message,
            attempt_id=attempt_id,
            completion_source="internal_failure",
        )

    def is_terminal(self, task_id: str) -> bool:
        record = self._records.get(task_id)
        return bool(record and record.status in _TASK_TERMINAL_STATUSES)

    def cancel(self, task_id: str, *, reason: str = "Task cancelled", payload: Optional[Dict[str, Any]] = None) -> Optional[RuntimeTaskRecord]:
        record = self._records.get(task_id)
        if record is None:
            return None
        if record.status in _TASK_TERMINAL_STATUSES:
            return record
        if record.background_task is not None and not record.background_task.done():
            record.background_task.cancel()
        record.status = "cancelled"
        record.finished_at = _utc_now_iso()
        record.error_message = reason
        record.payload = dict(payload or {"ok": False, "task_id": task_id, "execution_type": "task", "request_id": record.request_id, "status": "cancelled", "error": reason})
        record.cancel_requested = True
        record.active_attempt_id = None
        record.completion_source = "cancel"
        self._persist_record(record)
        return record

    def mark_stale(self, task_id: str, *, reason: str = "Task stale", payload: Optional[Dict[str, Any]] = None) -> Optional[RuntimeTaskRecord]:
        return self.mark_terminal(task_id, status="stale", payload=dict(payload or {"ok": False, "task_id": task_id, "execution_type": "task", "request_id": (self._records.get(task_id).request_id if self._records.get(task_id) else None), "status": "stale", "error": reason}), error_message=reason)

    def get(self, task_id: str) -> Optional[RuntimeTaskRecord]:
        return self._records.get(task_id)

    def remove(self, task_id: str) -> None:
        self._records.pop(task_id, None)
        self._delete_record_file(task_id)

    def list_active(self) -> list[RuntimeTaskRecord]:
        return [record for record in self._records.values() if record.status not in _TASK_TERMINAL_STATUSES]

    def mark_resuming(self, task_id: str) -> Optional[RuntimeTaskRecord]:
        record = self._records.get(task_id)
        if record is None or record.status in _TASK_TERMINAL_STATUSES:
            return record
        record.resume_count += 1
        record.last_resumed_at = _utc_now_iso()
        # A persisted active attempt belonged to a previous process. Clear it so
        # the replacement process owns a fresh attempt and late stale results
        # cannot be confused with the resumed run.
        record.active_attempt_id = None
        self._persist_record(record)
        return record

    def update_observation(
        self,
        task_id: str,
        *,
        pending_permission_request: Optional[Dict[str, Any]] = None,
        pending_question_request: Optional[Dict[str, Any]] = None,
        completion_source: Optional[str] = None,
        progress: Optional[Dict[str, Any]] = None,
    ) -> Optional[RuntimeTaskRecord]:
        record = self._records.get(task_id)
        if record is None:
            return None
        changed = False
        if pending_permission_request is not None:
            record.pending_permission_request = _optional_json_dict(pending_permission_request)
            changed = True
        if pending_question_request is not None:
            record.pending_question_request = _optional_json_dict(pending_question_request)
            changed = True
        if completion_source:
            record.completion_source = completion_source
            changed = True
        if progress is not None:
            record.last_progress_at = _utc_now_iso()
            payload = dict(record.payload or {})
            payload["progress"] = _json_safe(progress)
            record.payload = payload
            changed = True
        if changed:
            self._persist_record(record)
        return record

    def admission_matches(
        self,
        record: RuntimeTaskRecord,
        *,
        task_type: str,
        source: Optional[str],
        session_id: Optional[str],
        task_session_id: Optional[str],
        context_ref: Optional[Dict[str, Any]],
        merged_input_payload: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]],
    ) -> bool:
        expected = build_task_input_hash(
            task_type=task_type,
            source=source,
            session_id=session_id,
            task_session_id=task_session_id,
            context_ref=context_ref,
            merged_input_payload=merged_input_payload,
            metadata=metadata,
        )
        return bool(record.input_hash and record.input_hash == expected)

    def load_persisted_records(self) -> int:
        if self._storage_dir is None or not self._storage_dir.exists():
            return 0
        loaded: list[RuntimeTaskRecord] = []
        for path in sorted(self._storage_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                record = _record_from_json(raw)
            except Exception:
                continue
            loaded.append(record)

        loaded.sort(key=lambda record: record.accepted_at or "")
        for record in loaded:
            existing = self._records.get(record.task_id)
            if existing is not None and existing.background_task is not None and not existing.background_task.done():
                continue
            self._records[record.task_id] = record
            self._records.move_to_end(record.task_id)
        self.prune()
        return len(loaded)

    def prune(self) -> None:
        while len(self._records) > self._max_records:
            removable_task_id: Optional[str] = None
            for task_id, record in self._records.items():
                if record.status in _TASK_TERMINAL_STATUSES:
                    removable_task_id = task_id
                    break
            if removable_task_id is None:
                break
            self._records.pop(removable_task_id, None)
            self._delete_record_file(removable_task_id)

    def reset(self, *, clear_storage: bool = True) -> None:
        self._records.clear()
        if clear_storage and self._storage_dir is not None and self._storage_dir.exists():
            for path in self._storage_dir.glob("*.json"):
                try:
                    path.unlink()
                except OSError:
                    pass

    def _persist_record(self, record: RuntimeTaskRecord) -> None:
        if self._storage_dir is None:
            return
        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            path = self._record_path(record.task_id)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(_record_to_json(record), ensure_ascii=False, sort_keys=True), encoding="utf-8")
            tmp_path.replace(path)
        except (OSError, TypeError, ValueError):
            return

    def _delete_record_file(self, task_id: str) -> None:
        if self._storage_dir is None:
            return
        try:
            self._record_path(task_id).unlink(missing_ok=True)
        except OSError:
            return

    def _record_path(self, task_id: str) -> Path:
        assert self._storage_dir is not None
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        return self._storage_dir / f"{digest}.json"


def _optional_json_dict(value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    safe = _json_safe(value)
    return safe if isinstance(safe, dict) else dict(value)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


_VOLATILE_METADATA_KEYS = {
    "trace_id",
    "span_id",
    "parent_span_id",
    "portal_dispatch_id",
    "path",
    "external_triggered",
    "auto_run",
    "runtime_task_resumed",
    "runtime_task_resume_count",
    "runtime_task_admission_id",
    "runtime_task_input_hash",
    "runtime_task_session_id",
    "runtime_task_attempt_id",
    "runtime_task_attempt_count",
}


def _stable_metadata(value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return {key: _json_safe(item) for key, item in sorted(value.items()) if key not in _VOLATILE_METADATA_KEYS}


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_task_input_hash(
    *,
    task_type: str,
    source: Optional[str],
    session_id: Optional[str],
    task_session_id: Optional[str],
    context_ref: Optional[Dict[str, Any]],
    merged_input_payload: Optional[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
) -> str:
    return _stable_digest(
        {
            "task_type": str(task_type or ""),
            "source": source or "",
            "session_id": session_id or "",
            "task_session_id": task_session_id or "",
            "context_ref": _optional_json_dict(context_ref),
            "merged_input_payload": _optional_json_dict(merged_input_payload),
            "metadata": _stable_metadata(metadata),
        }
    )


def _admission_id(*, task_id: str, input_hash: str) -> str:
    digest = _stable_digest({"task_id": task_id, "input_hash": input_hash})
    return f"task_input_{digest[:24]}"


def _attempt_id(*, task_id: str, attempt_count: int, started_at: str) -> str:
    digest = _stable_digest({"task_id": task_id, "attempt_count": attempt_count, "started_at": started_at})
    return f"task_attempt_{digest[:24]}"


def _find_observation(value: Any, key: str) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            return _optional_json_dict(candidate)
        for nested in value.values():
            found = _find_observation(nested, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_observation(item, key)
            if found is not None:
                return found
    return None


def _apply_observation_from_payload(record: RuntimeTaskRecord, payload: Dict[str, Any]) -> None:
    permission_request = _find_observation(payload, "pending_permission_request")
    question_request = _find_observation(payload, "pending_question_request")
    if permission_request is not None:
        record.pending_permission_request = permission_request
    else:
        record.pending_permission_request = None
    if question_request is not None:
        record.pending_question_request = question_request
    else:
        record.pending_question_request = None


def _record_to_json(record: RuntimeTaskRecord) -> Dict[str, Any]:
    return _json_safe(
        {
            "task_id": record.task_id,
            "request_id": record.request_id,
            "task_type": record.task_type,
            "source": record.source,
            "session_id": record.session_id,
            "agent_id": record.agent_id,
            "status": record.status,
            "accepted_at": record.accepted_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "trace_id": record.trace_id,
            "portal_dispatch_id": record.portal_dispatch_id,
            "portal_task_id": record.portal_task_id,
            "payload": record.payload,
            "error_message": record.error_message,
            "context_ref": record.context_ref,
            "merged_input_payload": record.merged_input_payload,
            "metadata": record.metadata,
            "trace_headers": record.trace_headers,
            "resume_count": record.resume_count,
            "last_resumed_at": record.last_resumed_at,
            "admission_id": record.admission_id,
            "admitted_at": record.admitted_at,
            "input_hash": record.input_hash,
            "task_session_id": record.task_session_id,
            "active_attempt_id": record.active_attempt_id,
            "attempt_count": record.attempt_count,
            "last_attempt_started_at": record.last_attempt_started_at,
            "last_progress_at": record.last_progress_at,
            "pending_permission_request": record.pending_permission_request,
            "pending_question_request": record.pending_question_request,
            "completion_source": record.completion_source,
            "cancel_requested": record.cancel_requested,
        }
    )


def _record_from_json(raw: Dict[str, Any]) -> RuntimeTaskRecord:
    if not isinstance(raw, dict):
        raise ValueError("persisted runtime task record must be an object")
    task_id = str(raw.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("persisted runtime task record missing task_id")
    metadata = _optional_json_dict(raw.get("metadata"))
    context_ref = _optional_json_dict(raw.get("context_ref"))
    merged_input_payload = _optional_json_dict(raw.get("merged_input_payload"))
    task_type = str(raw.get("task_type") or "")
    source = str(raw.get("source") or "portal")
    session_id = str(raw["session_id"]) if raw.get("session_id") is not None else None
    task_session_id = str(raw["task_session_id"]) if raw.get("task_session_id") is not None else None
    input_hash = str(raw["input_hash"]) if raw.get("input_hash") is not None else build_task_input_hash(
        task_type=task_type,
        source=source,
        session_id=session_id,
        task_session_id=task_session_id,
        context_ref=context_ref,
        merged_input_payload=merged_input_payload,
        metadata=metadata,
    )
    return RuntimeTaskRecord(
        task_id=task_id,
        request_id=str(raw.get("request_id") or f"task-{task_id}"),
        task_type=task_type,
        source=source,
        session_id=session_id,
        agent_id=str(raw["agent_id"]) if raw.get("agent_id") is not None else None,
        status=str(raw.get("status") or "accepted"),
        accepted_at=str(raw.get("accepted_at") or _utc_now_iso()),
        started_at=str(raw["started_at"]) if raw.get("started_at") is not None else None,
        finished_at=str(raw["finished_at"]) if raw.get("finished_at") is not None else None,
        trace_id=str(raw["trace_id"]) if raw.get("trace_id") is not None else None,
        portal_dispatch_id=str(raw["portal_dispatch_id"]) if raw.get("portal_dispatch_id") is not None else None,
        portal_task_id=str(raw["portal_task_id"]) if raw.get("portal_task_id") is not None else task_id,
        payload=dict(raw.get("payload") or {}),
        error_message=str(raw["error_message"]) if raw.get("error_message") is not None else None,
        context_ref=context_ref,
        merged_input_payload=merged_input_payload,
        metadata=metadata,
        trace_headers=_optional_json_dict(raw.get("trace_headers")),
        resume_count=int(raw.get("resume_count") or 0),
        last_resumed_at=str(raw["last_resumed_at"]) if raw.get("last_resumed_at") is not None else None,
        admission_id=str(raw["admission_id"]) if raw.get("admission_id") is not None else _admission_id(task_id=task_id, input_hash=input_hash),
        admitted_at=str(raw["admitted_at"]) if raw.get("admitted_at") is not None else str(raw.get("accepted_at") or _utc_now_iso()),
        input_hash=input_hash,
        task_session_id=task_session_id,
        active_attempt_id=str(raw["active_attempt_id"]) if raw.get("active_attempt_id") is not None else None,
        attempt_count=int(raw.get("attempt_count") or 0),
        last_attempt_started_at=str(raw["last_attempt_started_at"]) if raw.get("last_attempt_started_at") is not None else None,
        last_progress_at=str(raw["last_progress_at"]) if raw.get("last_progress_at") is not None else None,
        pending_permission_request=_optional_json_dict(raw.get("pending_permission_request")),
        pending_question_request=_optional_json_dict(raw.get("pending_question_request")),
        completion_source=str(raw["completion_source"]) if raw.get("completion_source") is not None else None,
        cancel_requested=bool(raw.get("cancel_requested")),
        background_task=None,
    )
