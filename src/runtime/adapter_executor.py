"""Adapter-backed runtime action execution helpers."""

from __future__ import annotations

from typing import Any, Dict

from src.runtime.events import build_runtime_event


def _event(event_type: str, state: str, detail_payload: Dict[str, Any]) -> Dict[str, Any]:
    return build_runtime_event(
        event_type=event_type,
        execution_type="task",
        state=state,
        session_id=None,
        request_id=None,
        agent_id=None,
        summary=event_type,
        detail_payload=detail_payload,
        legacy_payload={"legacy_type": event_type.replace(".", "_")},
    )


def _result_success(value: Any) -> bool:
    if isinstance(value, dict) and isinstance(value.get("success"), bool):
        return bool(value.get("success"))
    text = str(value or "")
    lowered = text.lower()
    return not (
        lowered.startswith("error")
        or " error:" in lowered
        or lowered.startswith("cannot")
        or lowered.startswith("failed")
    )


async def execute_jira_workflow_action(action_name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    from src import jira as jira_module

    action = str(action_name or "").strip()
    payload = dict(kwargs or {})

    if action == "read_issue":
        issue_key = payload.get("issue_key")
        if not issue_key:
            return {"success": False, "error": "issue_key is required", "system": "jira", "action_name": action}
        raw = await jira_module.jira_get_issue(issue_key)
    elif action == "update_issue":
        issue_key = payload.get("issue_key")
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        if not issue_key:
            return {"success": False, "error": "issue_key is required", "system": "jira", "action_name": action}
        raw = await jira_module.jira_update_issue(
            issue_key=issue_key,
            summary=fields.get("summary"),
            description=fields.get("description"),
        )
    elif action == "assign_issue":
        issue_key = payload.get("issue_key")
        assignee = payload.get("assignee")
        if not issue_key:
            return {"success": False, "error": "issue_key is required", "system": "jira", "action_name": action}
        raw = await jira_module.jira_assign_issue(issue_key=issue_key, assignee=assignee)
    elif action == "transition_issue":
        issue_key = payload.get("issue_key")
        transition = payload.get("transition") or payload.get("to_status")
        comment = payload.get("comment")
        if not issue_key or not transition:
            return {
                "success": False,
                "error": "issue_key and transition are required",
                "system": "jira",
                "action_name": action,
            }
        raw = await jira_module.jira_transition(issue_key=issue_key, to_status=transition, comment=comment)
    elif action == "add_comment":
        issue_key = payload.get("issue_key")
        comment = payload.get("comment") or payload.get("body")
        if not issue_key or not comment:
            return {
                "success": False,
                "error": "issue_key and comment are required",
                "system": "jira",
                "action_name": action,
            }
        raw = await jira_module.jira_add_comment(issue_key=issue_key, comment=comment)
    else:
        return {"success": False, "error": f"Unsupported jira action: {action}", "system": "jira", "action_name": action}

    success = _result_success(raw)
    return {
        "success": success,
        "error": None if success else str(raw),
        "system": "jira",
        "action_name": action,
        "result": raw,
    }


async def execute_adapter_action(action_id: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    normalized_action_id = str(action_id or "").strip().lower()
    payload = dict(kwargs or {})

    runtime_events = [_event("task.adapter_action.started", "started", {"action_id": normalized_action_id, "system": "jira"})]

    action_map = {
        "adapter:jira:read_issue": "read_issue",
        "adapter:jira:update_issue": "update_issue",
        "adapter:jira:assign_issue": "assign_issue",
        "adapter:jira:transition_issue": "transition_issue",
    }
    jira_action = action_map.get(normalized_action_id)
    if jira_action is None:
        runtime_events.append(
            _event(
                "task.adapter_action.failed",
                "failed",
                {"action_id": normalized_action_id, "system": "jira", "error": "unsupported_adapter_action"},
            )
        )
        return {
            "success": False,
            "error": f"Unsupported adapter action: {action_id}",
            "action_id": normalized_action_id,
            "system": "jira",
            "runtime_events": runtime_events,
        }

    outcome = await execute_jira_workflow_action(jira_action, payload)
    runtime_events.append(
        _event(
            "task.adapter_action.completed" if outcome.get("success") else "task.adapter_action.failed",
            "completed" if outcome.get("success") else "failed",
            {
                "action_id": normalized_action_id,
                "system": "jira",
                "success": bool(outcome.get("success")),
                "error": outcome.get("error"),
            },
        )
    )
    return {
        "action_id": normalized_action_id,
        "system": "jira",
        "success": bool(outcome.get("success")),
        "error": outcome.get("error"),
        "result": outcome.get("result"),
        "runtime_events": runtime_events,
    }
