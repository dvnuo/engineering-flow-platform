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
    state: str,
    session_id: Optional[str],
    request_id: Optional[str],
    agent_id: Optional[str],
    summary: str,
    detail_payload: Optional[Dict[str, Any]] = None,
    legacy_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_detail = normalize_event_payload(detail_payload)
    event = {
        "event_type": event_type,
        "state": state,
        "session_id": session_id,
        "request_id": request_id,
        "agent_id": agent_id,
        "summary": summary,
        "detail_payload": normalized_detail,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    if legacy_payload:
        event.update(legacy_payload)
    return event
