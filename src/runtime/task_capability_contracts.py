"""Canonical wrapper-task capability contract resolution.

This module is the single source of truth for wrapper-task -> capability
mapping in the runtime capability surface. Execution and governance must both
consume this resolver instead of maintaining parallel task-branch mapping
logic in their own modules.

The returned adapter action ids and involved capability ids are part of the
runtime capability-surface contract and are intentionally centralized here so
policy, auditing, and capability metadata remain consistent across call paths.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.runtime.bundle_template_registry import get_bundle_action
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
            if not action_id:
                return fallback
            return {
                **fallback,
                "primary_capability_id": action_id,
                "capability_id": action_id,
                "capability_type": "adapter_action",
                "action_id": action_id,
                "involved_capability_ids": [action_id],
            }
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
            return {
                **fallback,
                "primary_capability_id": primary_capability_id,
                "capability_id": primary_capability_id,
                "capability_type": "adapter_action",
                "action_id": primary_capability_id,
                "involved_capability_ids": involved,
            }
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
        skill_name = str(normalized_payload.get("skill_name") or "review-pull-request").strip().lower() or "review-pull-request"
        primary_capability_id = f"skill:{skill_name}"
        descriptor = registry.get(primary_capability_id)
        involved = _resolve_involved_capability_ids_for_task(normalized_task_type, normalized_payload)
        if descriptor is None:
            return {
                **fallback,
                "primary_capability_id": primary_capability_id,
                "capability_id": primary_capability_id,
                "action_id": primary_capability_id,
                "capability_type": "skill",
                "involved_capability_ids": involved,
            }
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

    if normalized_task_type == "bundle_action_task":
        template_id = str(normalized_payload.get("template_id") or "").strip().lower()
        action_id = str(normalized_payload.get("action_id") or "").strip().lower()
        action = get_bundle_action(template_id, action_id)
        if action is None:
            return fallback
        primary_capability_id = f"skill:{action.skill_name}"
        descriptor = registry.get(primary_capability_id)
        involved = [primary_capability_id]
        if descriptor is None:
            return {
                **fallback,
                "primary_capability_id": primary_capability_id,
                "capability_id": primary_capability_id,
                "action_id": primary_capability_id,
                "capability_type": "skill",
                "involved_capability_ids": involved,
            }
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


    if normalized_task_type == "requirement_bundle_collect_task":
        primary_capability_id = "skill:collect_requirements_to_bundle"
        descriptor = registry.get(primary_capability_id)
        involved = [primary_capability_id]
        if descriptor is None:
            return {
                **fallback,
                "primary_capability_id": primary_capability_id,
                "capability_id": primary_capability_id,
                "action_id": primary_capability_id,
                "capability_type": "skill",
                "involved_capability_ids": involved,
            }
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

    if normalized_task_type == "requirement_bundle_design_test_cases_task":
        primary_capability_id = "skill:design_test_cases_from_bundle"
        descriptor = registry.get(primary_capability_id)
        involved = [primary_capability_id]
        if descriptor is None:
            return {
                **fallback,
                "primary_capability_id": primary_capability_id,
                "capability_id": primary_capability_id,
                "action_id": primary_capability_id,
                "capability_type": "skill",
                "involved_capability_ids": involved,
            }
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
            return {
                **fallback,
                "primary_capability_id": capability_id,
                "capability_id": capability_id,
                "action_id": capability_id,
                "involved_capability_ids": [capability_id],
                "capability_type": "skill",
            }
        return {
            **fallback,
            "primary_capability_id": descriptor.capability_id,
            "capability_id": descriptor.capability_id,
            "action_id": descriptor.capability_id,
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
        def _is_non_empty_mapping(value: Any) -> bool:
            return isinstance(value, dict) and bool(value)

        involved = {"adapter:jira:read_issue"}
        has_transition = any(payload.get(key) for key in ("transition", "success_transition", "failure_transition"))
        has_assign = any(
            payload.get(key)
            for key in ("assignee", "success_reassign_to", "failure_reassign_to", "explicit_success_assignee", "explicit_failure_assignee")
        )
        has_comment = any(payload.get(key) for key in ("review_comment", "review_comment_template", "transition_comment"))
        has_update = any(
            _is_non_empty_mapping(payload.get(key))
            for key in ("fields", "fields_on_success", "fields_on_failure")
        )
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
        skill_name = str(payload.get("skill_name") or "review-pull-request").strip().lower() or "review-pull-request"
        writeback_mode = str(payload.get("writeback_mode") or "").strip().lower()
        secondary = "adapter:github:add_comment" if writeback_mode == "issue_comment" else "adapter:github:review_pull_request"
        return [secondary, f"skill:{skill_name}"]
    return []
