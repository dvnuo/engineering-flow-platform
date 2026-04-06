"""Runtime event normalization helpers."""

from datetime import datetime
from typing import Any, Dict, Optional


def normalize_event_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    return {"value": payload}


def build_runtime_event(
    *,
    event_type: str,
    execution_type: Optional[str] = None,
    state: str,
    session_id: Optional[str],
    request_id: Optional[str],
    agent_id: Optional[str],
    summary: str,
    task_id: Optional[str] = None,
    detail_payload: Optional[Dict[str, Any]] = None,
    legacy_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_detail = normalize_event_payload(detail_payload)
    resolved_execution_type = execution_type
    if not resolved_execution_type and isinstance(normalized_detail.get("execution_type"), str):
        resolved_execution_type = normalized_detail.get("execution_type")
    created_at = datetime.utcnow().isoformat() + "Z"
    event = {
        "event": event_type,
        "event_type": event_type,
        "execution_id": request_id,
        "state": state,
        "session_id": session_id,
        "request_id": request_id,
        "agent_id": agent_id,
        "summary": summary,
        "payload": normalized_detail,
        "detail_payload": normalized_detail,
        "timestamp": created_at,
        "created_at": created_at,
    }
    if isinstance(resolved_execution_type, str) and resolved_execution_type.strip():
        event["type"] = resolved_execution_type.strip()
        event["execution_type"] = resolved_execution_type.strip()
    if task_id is not None:
        event["task_id"] = task_id
    if legacy_payload:
        for key, value in legacy_payload.items():
            event.setdefault(key, value)
    return event
