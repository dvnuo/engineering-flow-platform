"""Best-effort Runtime -> Portal session metadata publish helpers."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from aiohttp import ClientSession

from src.runtime.contracts import ExecutionResult
from src.utils.internal_api_keys import build_portal_internal_api_headers, get_portal_internal_base_url

logger = logging.getLogger(__name__)

_MAX_RUNTIME_EVENTS = 20
_CONTROL_PLANE_METADATA_KEYS = {
    "portal_task_id",
    "task_id",
    "group_id",
    "delegation_id",
    "coordination_run_id",
    "current_task_id",
    "current_delegation_id",
    "current_coordination_run_id",
    "source_type",
    "source_ref",
    "workflow_rule_id",
    "portal_workflow_rule_id",
    "portal_task_source",
    "shared_context_ref",
}


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _normalized_state(status: Optional[str], *, fallback: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"success", "completed", "ok"}:
        return "success"
    if normalized in {"error", "failed", "failure"}:
        return "error"
    if normalized in {"blocked", "denied"}:
        return "blocked"
    if normalized in {"queued", "started", "running", "in_progress"}:
        return "running"
    return fallback


def build_session_metadata_payload(
    *,
    last_execution_id: Optional[str],
    latest_event_type: Optional[str],
    latest_event_state: Optional[str],
    snapshot_version: Optional[str],
    runtime_events: Optional[list[Dict[str, Any]]],
    metadata: Optional[Dict[str, Any]],
    pending_delegations: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    metadata = dict(metadata or {})
    payload: Dict[str, Any] = {}

    group_id = metadata.get("group_id")
    if group_id:
        payload["group_id"] = str(group_id)

    for src_key, dst_key in (
        ("current_task_id", "current_task_id"),
        ("current_delegation_id", "current_delegation_id"),
        ("current_coordination_run_id", "current_coordination_run_id"),
        ("source_type", "source_type"),
        ("source_ref", "source_ref"),
    ):
        value = metadata.get(src_key)
        if value not in (None, ""):
            payload[dst_key] = value

    if last_execution_id:
        payload["last_execution_id"] = str(last_execution_id)
    if latest_event_type:
        payload["latest_event_type"] = str(latest_event_type)
    if latest_event_state:
        payload["latest_event_state"] = str(latest_event_state)
    if snapshot_version:
        payload["snapshot_version"] = str(snapshot_version)

    if pending_delegations:
        payload["pending_delegations_json"] = _safe_json(pending_delegations)

    trimmed_runtime_events = list(runtime_events or [])[-_MAX_RUNTIME_EVENTS:]
    if trimmed_runtime_events:
        payload["runtime_events_json"] = _safe_json(trimmed_runtime_events)

    control_plane_metadata = {
        key: metadata[key]
        for key in _CONTROL_PLANE_METADATA_KEYS
        if key in metadata and metadata[key] not in (None, "")
    }
    if control_plane_metadata:
        payload["metadata_json"] = _safe_json(control_plane_metadata)

    return payload


def extract_session_metadata_publish_fields(
    execution_result: ExecutionResult,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    default_event_type: str,
    default_state: str,
) -> Dict[str, Any]:
    runtime_events = execution_result.runtime_events if isinstance(execution_result.runtime_events, list) else []
    latest_event = runtime_events[-1] if runtime_events and isinstance(runtime_events[-1], dict) else {}

    latest_event_type = str(latest_event.get("event_type") or default_event_type)
    latest_event_state = _normalized_state(latest_event.get("state") or execution_result.status, fallback=default_state)

    artifacts = execution_result.artifacts if isinstance(execution_result.artifacts, dict) else {}
    recovery = artifacts.get("recovery") if isinstance(artifacts.get("recovery"), dict) else {}
    snapshot_version = recovery.get("snapshot_version") or artifacts.get("snapshot_version")

    output_payload = execution_result.output_payload if isinstance(execution_result.output_payload, dict) else {}
    if not snapshot_version:
        snapshot_version = output_payload.get("snapshot_version")

    return {
        "last_execution_id": execution_result.request_id,
        "latest_event_type": latest_event_type,
        "latest_event_state": latest_event_state,
        "snapshot_version": snapshot_version,
        "runtime_events": runtime_events,
        "metadata": dict(metadata or {}),
    }


async def publish_session_metadata(
    *,
    agent_id: str,
    session_id: str,
    last_execution_id: Optional[str],
    latest_event_type: Optional[str],
    latest_event_state: Optional[str],
    snapshot_version: Optional[str],
    runtime_events: Optional[list[Dict[str, Any]]],
    metadata: Optional[Dict[str, Any]],
    pending_delegations: Optional[list[Dict[str, Any]]] = None,
) -> None:
    normalized_agent_id = str(agent_id or "").strip()
    normalized_session_id = str(session_id or "").strip()
    if not normalized_agent_id or not normalized_session_id:
        return

    base_url = get_portal_internal_base_url()
    if not base_url:
        return

    payload = build_session_metadata_payload(
        last_execution_id=last_execution_id,
        latest_event_type=latest_event_type,
        latest_event_state=latest_event_state,
        snapshot_version=snapshot_version,
        runtime_events=runtime_events,
        metadata=metadata,
        pending_delegations=pending_delegations,
    )

    if not payload:
        return

    url = f"{base_url}/api/internal/agents/{normalized_agent_id}/sessions/{normalized_session_id}/metadata"
    headers = build_portal_internal_api_headers(include_content_type=True)

    try:
        async with ClientSession(headers=headers) as session:
            async with session.put(url, json=payload) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.warning(
                        "Session metadata publish failed: status=%s agent_id=%s session_id=%s body=%s",
                        response.status,
                        normalized_agent_id,
                        normalized_session_id,
                        body[:500],
                    )
    except Exception:
        logger.warning(
            "Session metadata publish failed for agent_id=%s session_id=%s",
            normalized_agent_id,
            normalized_session_id,
            exc_info=True,
        )
