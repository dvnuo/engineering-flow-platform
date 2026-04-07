"""Runtime helper surface for leader-driven Portal delegation creation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.runtime.adapter_executor import execute_adapter_action


REQUIRED_DELEGATION_FIELDS = ("group_id", "leader_agent_id", "assignee_agent_id", "objective")
STRUCTURED_FIELDS = (
    "scoped_context_payload",
    "input_artifacts",
    "expected_output_schema",
    "retry_policy",
    "skill_kwargs",
)
PASSTHROUGH_FIELDS = ("coordination_run_id", "round_index", "leader_session_id")
TASK_COORDINATION_FIELDS = (
    "agent_mode",
    "skill_name",
    "selection_strategy",
    "template_agent_id",
    "task_agent_template_id",
    "ephemeral_task_agent_id",
    "task_agent_scope",
    "task_agent_scope_label",
    "task_agent_cleanup_policy",
    "scope_label",
    "name",
)


def normalize_leader_delegation_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    request_payload = dict(payload or {})
    missing = [key for key in REQUIRED_DELEGATION_FIELDS if not str(request_payload.get(key) or "").strip()]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    normalized = dict(request_payload)
    normalized["group_id"] = str(request_payload["group_id"]).strip()
    normalized["leader_agent_id"] = str(request_payload["leader_agent_id"]).strip()
    normalized["assignee_agent_id"] = str(request_payload["assignee_agent_id"]).strip()
    normalized["objective"] = str(request_payload["objective"]).strip()
    normalized["visibility"] = str(request_payload.get("visibility") or "leader_only").strip() or "leader_only"
    normalized["skill_name"] = str(request_payload.get("skill_name") or "delegation").strip() or "delegation"
    normalized["parent_agent_id"] = str(
        request_payload.get("parent_agent_id") or request_payload["leader_agent_id"]
    ).strip()
    for key in STRUCTURED_FIELDS:
        if key in request_payload:
            normalized[key] = request_payload.get(key)
    for key in PASSTHROUGH_FIELDS:
        if key in request_payload and request_payload.get(key) is not None:
            normalized[key] = request_payload.get(key)
    for key in TASK_COORDINATION_FIELDS:
        if key in request_payload and request_payload.get(key) is not None:
            normalized[key] = request_payload.get(key)
    return normalized


def _normalize_create_result(outcome: Dict[str, Any]) -> Dict[str, Any]:
    result_payload = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    delegation_id = result_payload.get("delegation_id") or result_payload.get("id")
    return {
        "success": bool(outcome.get("success")),
        "delegation_id": delegation_id,
        "error": outcome.get("error"),
        "result": outcome.get("result"),
    }


async def create_portal_delegation_from_runtime(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        normalized = normalize_leader_delegation_request(payload)
    except ValueError as exc:
        return {"success": False, "delegation_id": None, "error": str(exc), "result": None}
    outcome = await execute_adapter_action("adapter:portal:create_delegation", normalized)
    return _normalize_create_result(outcome)


async def create_specialist_delegation(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_leader_delegation_request(payload)
    normalized["agent_mode"] = str(normalized.get("agent_mode") or "specialist").strip() or "specialist"
    normalized["skill_name"] = str(normalized.get("skill_name") or "delegation").strip() or "delegation"
    outcome = await execute_adapter_action("adapter:portal:create_delegation", normalized)
    return _normalize_create_result(outcome)


async def create_task_agent_delegation(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_leader_delegation_request(payload)
    ephemeral_task_agent_id: Optional[str] = str(normalized.get("ephemeral_task_agent_id") or "").strip() or None
    task_agent_scope: Optional[str] = str(normalized.get("task_agent_scope") or "").strip() or None
    if not ephemeral_task_agent_id or not task_agent_scope:
        return {
            "success": False,
            "delegation_id": None,
            "error": "ephemeral_task_agent_id and task_agent_scope are required for task agent delegation",
            "result": None,
        }
    normalized["agent_mode"] = "task"
    normalized["skill_name"] = str(normalized.get("skill_name") or "delegation").strip() or "delegation"
    normalized["ephemeral_task_agent_id"] = ephemeral_task_agent_id
    normalized["task_agent_scope"] = task_agent_scope
    if normalized.get("task_agent_template_id") is not None:
        normalized["task_agent_template_id"] = str(normalized.get("task_agent_template_id")).strip() or None
    if normalized.get("task_agent_cleanup_policy") is not None:
        normalized["task_agent_cleanup_policy"] = str(normalized.get("task_agent_cleanup_policy")).strip() or None
    outcome = await execute_adapter_action("adapter:portal:create_delegation", normalized)
    return _normalize_create_result(outcome)


async def get_group_specialist_pool(group_id: str) -> Dict[str, Any]:
    normalized_group_id = str(group_id or "").strip()
    if not normalized_group_id:
        return {"success": False, "error": "group_id is required", "result": None}
    outcome = await execute_adapter_action("adapter:portal:get_specialist_pool", {"group_id": normalized_group_id})
    return {"success": bool(outcome.get("success")), "error": outcome.get("error"), "result": outcome.get("result")}


async def create_task_agent_for_group(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload or {})
    required_fields = ("group_id", "leader_agent_id", "template_agent_id", "name")
    for key in required_fields:
        normalized[key] = str(normalized.get(key) or "").strip()
    missing = [key for key in required_fields if not normalized.get(key)]
    if missing:
        return {"success": False, "error": f"Missing required fields: {', '.join(missing)}", "result": None}
    outcome = await execute_adapter_action("adapter:portal:create_task_agent", normalized)
    return {"success": bool(outcome.get("success")), "error": outcome.get("error"), "result": outcome.get("result")}


async def delete_task_agent_for_group(group_id: str, agent_id: str) -> Dict[str, Any]:
    normalized_group_id = str(group_id or "").strip()
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_group_id or not normalized_agent_id:
        return {"success": False, "error": "group_id and agent_id are required", "result": None}
    outcome = await execute_adapter_action(
        "adapter:portal:delete_task_agent",
        {"group_id": normalized_group_id, "agent_id": normalized_agent_id},
    )
    return {"success": bool(outcome.get("success")), "error": outcome.get("error"), "result": outcome.get("result")}
