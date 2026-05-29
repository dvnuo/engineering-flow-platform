"""Best-effort Runtime -> Portal session metadata publish helpers."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from aiohttp import ClientSession

from src.runtime.contracts import ExecutionResult
from src.utils.portal_internal_api import build_portal_internal_api_headers, get_portal_internal_base_url

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
    "context_compaction_level",
    "context_objective_preview",
    "context_summary_preview",
    "context_next_step_preview",
    "context_usage_percent",
    "context_estimated_tokens",
    "context_window_tokens",
    "context_next_compaction_action",
    "context_next_pruning_policy",
    "context_tokens_until_soft_threshold",
    "context_tokens_until_hard_threshold",
    "context_state",
    "active_skill_name",
    "active_skill_status",
    "active_skill_goal",
    "active_skill_hash",
    "active_skill_turn_count",
    "active_skill_activation_reason",
    "active_skill_tool_policy_declared",
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


def _first_non_empty(metadata: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = metadata.get(key)
        if value not in (None, ""):
            return value
    return None


def _optional_string(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


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

    group_id = _first_non_empty(metadata, "group_id", "portal_group_id")
    normalized_group_id = _optional_string(group_id)
    if normalized_group_id is not None:
        payload["group_id"] = normalized_group_id

    current_task_id = _first_non_empty(metadata, "current_task_id", "portal_task_id", "task_id")
    normalized_current_task_id = _optional_string(current_task_id)
    if normalized_current_task_id is not None:
        payload["current_task_id"] = normalized_current_task_id

    current_delegation_id = _first_non_empty(metadata, "current_delegation_id", "portal_delegation_id", "delegation_id")
    normalized_current_delegation_id = _optional_string(current_delegation_id)
    if normalized_current_delegation_id is not None:
        payload["current_delegation_id"] = normalized_current_delegation_id

    current_coordination_run_id = _first_non_empty(
        metadata,
        "current_coordination_run_id",
        "portal_coordination_run_id",
        "coordination_run_id",
    )
    normalized_current_coordination_run_id = _optional_string(current_coordination_run_id)
    if normalized_current_coordination_run_id is not None:
        payload["current_coordination_run_id"] = normalized_current_coordination_run_id

    source_type = metadata.get("source_type")
    if source_type not in (None, ""):
        payload["source_type"] = source_type

    source_ref = _first_non_empty(metadata, "source_ref", "portal_task_id", "task_id")
    normalized_source_ref = _optional_string(source_ref)
    if normalized_source_ref is not None:
        payload["source_ref"] = normalized_source_ref

    if last_execution_id:
        payload["last_execution_id"] = str(last_execution_id)
    if latest_event_type:
        payload["latest_event_type"] = str(latest_event_type)
    if latest_event_state:
        payload["latest_event_state"] = str(latest_event_state)
    if snapshot_version:
        payload["snapshot_version"] = str(snapshot_version)

    if pending_delegations is not None:
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
    runtime_events_value = getattr(execution_result, "runtime_events", None)
    runtime_events = runtime_events_value if isinstance(runtime_events_value, list) else []
    latest_event = runtime_events[-1] if runtime_events and isinstance(runtime_events[-1], dict) else {}

    latest_event_type = str(latest_event.get("event_type") or default_event_type)
    result_status = getattr(execution_result, "status", None)
    latest_event_state = _normalized_state(latest_event.get("state") or result_status, fallback=default_state)

    artifacts_value = getattr(execution_result, "artifacts", None)
    artifacts = artifacts_value if isinstance(artifacts_value, dict) else {}
    recovery = artifacts.get("recovery") if isinstance(artifacts.get("recovery"), dict) else {}
    snapshot_version = recovery.get("snapshot_version") or artifacts.get("snapshot_version")

    output_payload_value = getattr(execution_result, "output_payload", None)
    output_payload = output_payload_value if isinstance(output_payload_value, dict) else {}
    if not snapshot_version:
        snapshot_version = output_payload.get("snapshot_version")

    return {
        "last_execution_id": getattr(execution_result, "request_id", None),
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
