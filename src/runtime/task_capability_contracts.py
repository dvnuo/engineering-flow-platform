"""Canonical task-wrapper capability contract resolution."""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.runtime.capability_registry import get_capability_registry


def resolve_task_capability_contract(task_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_task_type = str(task_type or "").strip().lower()
    normalized_payload = dict(payload or {})
    registry = get_capability_registry()

    fallback: Dict[str, Any] = {
        "primary_capability_id": None,
        "capability_id": None,
        "capability_type": None,
        "action_id": None,
        "involved_capability_ids": [],
        "policy_tags": [],
        "requires_identity_binding": False,
        "capability_resolution": "unresolved",
    }

    if normalized_task_type == "adapter_action_task":
        action_id = str(normalized_payload.get("action_id") or "").strip().lower()
        descriptor = registry.get(action_id) if action_id else None
        if descriptor is None:
            return {**fallback, "primary_capability_id": action_id or None, "capability_id": action_id or None, "action_id": action_id or None}
        return {
            **fallback,
            "primary_capability_id": descriptor.capability_id,
            "capability_id": descriptor.capability_id,
            "capability_type": descriptor.type,
            "action_id": descriptor.capability_id,
            "involved_capability_ids": [descriptor.capability_id],
            "policy_tags": list(descriptor.policy_tags or []),
            "requires_identity_binding": bool(descriptor.requires_identity_binding),
            "capability_resolution": "resolved",
        }

    if normalized_task_type == "jira_workflow_review_task":
        primary_capability_id = "adapter:jira:read_issue"
        descriptor = registry.get(primary_capability_id)
        involved = _resolve_involved_capability_ids_for_task(normalized_task_type, normalized_payload)
        if descriptor is None:
            return {**fallback, "primary_capability_id": primary_capability_id, "capability_id": primary_capability_id, "action_id": primary_capability_id, "involved_capability_ids": involved}
        return {
            **fallback,
            "primary_capability_id": descriptor.capability_id,
            "capability_id": descriptor.capability_id,
            "capability_type": descriptor.type,
            "action_id": descriptor.capability_id,
            "involved_capability_ids": involved,
            "policy_tags": list(descriptor.policy_tags or []),
            "requires_identity_binding": bool(descriptor.requires_identity_binding),
            "capability_resolution": "resolved",
        }

    if normalized_task_type == "github_review_task":
        primary_capability_id = "adapter:github:review_pull_request"
        descriptor = registry.get(primary_capability_id)
        involved = _resolve_involved_capability_ids_for_task(normalized_task_type, normalized_payload)
        if descriptor is None:
            return {**fallback, "primary_capability_id": primary_capability_id, "capability_id": primary_capability_id, "action_id": primary_capability_id, "involved_capability_ids": involved}
        return {
            **fallback,
            "primary_capability_id": descriptor.capability_id,
            "capability_id": descriptor.capability_id,
            "capability_type": descriptor.type,
            "action_id": descriptor.capability_id,
            "involved_capability_ids": involved,
            "policy_tags": list(descriptor.policy_tags or []),
            "requires_identity_binding": bool(descriptor.requires_identity_binding),
            "capability_resolution": "resolved",
        }

    if normalized_task_type == "delegation_task":
        skill_name = str(normalized_payload.get("skill_name") or "").strip().lower()
        if not skill_name:
            return fallback
        capability_id = f"skill:{skill_name}"
        descriptor = registry.get(capability_id)
        if descriptor is None:
            return {**fallback, "primary_capability_id": capability_id, "capability_id": capability_id, "involved_capability_ids": [capability_id], "capability_type": "skill"}
        return {
            **fallback,
            "primary_capability_id": descriptor.capability_id,
            "capability_id": descriptor.capability_id,
            "capability_type": descriptor.type,
            "involved_capability_ids": [descriptor.capability_id],
            "policy_tags": list(descriptor.policy_tags or []),
            "requires_identity_binding": bool(descriptor.requires_identity_binding),
            "capability_resolution": "resolved",
        }

    return fallback


def _resolve_involved_capability_ids_for_task(task_type: str, payload: Dict[str, Any]) -> list[str]:
    normalized_task_type = str(task_type or "").strip().lower()
    if normalized_task_type == "jira_workflow_review_task":
        involved = {"adapter:jira:read_issue"}
        has_transition = any(payload.get(key) for key in ("transition", "success_transition", "failure_transition"))
        has_assign = any(
            payload.get(key)
            for key in ("assignee", "success_reassign_to", "failure_reassign_to", "explicit_success_assignee", "explicit_failure_assignee")
        )
        has_comment = any(payload.get(key) for key in ("review_comment", "review_comment_template", "transition_comment"))
        has_update = bool(payload.get("fields"))
        if has_transition:
            involved.add("adapter:jira:transition_issue")
        if has_assign:
            involved.add("adapter:jira:assign_issue")
        if has_comment:
            involved.add("adapter:jira:add_comment")
        if has_update:
            involved.add("adapter:jira:update_issue")
        return sorted(involved)
    if normalized_task_type == "github_review_task":
        return ["adapter:github:add_comment", "adapter:github:review_pull_request"]
    return []
