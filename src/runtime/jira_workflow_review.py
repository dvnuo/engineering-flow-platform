"""Thin Jira workflow-aware runtime task orchestration."""

from __future__ import annotations

from typing import Any, Dict, List

from src.runtime.adapter_executor import execute_jira_workflow_action
from src.runtime.events import build_runtime_event


def _event(event_type: str, state: str, issue_key: str, detail_payload: Dict[str, Any]) -> Dict[str, Any]:
    return build_runtime_event(
        event_type=event_type,
        execution_type="task",
        state=state,
        session_id=None,
        request_id=None,
        agent_id=None,
        summary=f"jira workflow review {state}",
        detail_payload={"issue_key": issue_key, **detail_payload},
        legacy_payload={"legacy_type": event_type.replace(".", "_")},
    )


async def run_jira_workflow_review(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload or {})
    issue_key = data.get("issue_key")
    if not issue_key:
        return {
            "issue_key": None,
            "reviewed": False,
            "issue_snapshot": None,
            "actions_applied": [],
            "comment_added": False,
            "assignee_updated": None,
            "transitioned_to": None,
            "updated_fields": {},
            "success": False,
            "error": "issue_key is required",
            "runtime_events": [_event("task.jira_workflow_review.failed", "failed", "", {"error": "issue_key is required"})],
        }

    runtime_events: List[Dict[str, Any]] = []
    actions_applied: List[Dict[str, Any]] = []

    read_outcome = await execute_jira_workflow_action("read_issue", {"issue_key": issue_key})
    if not read_outcome.get("success"):
        runtime_events.append(
            _event(
                "task.jira_workflow_review.failed",
                "failed",
                issue_key,
                {"error": read_outcome.get("error")},
            )
        )
        return {
            "issue_key": issue_key,
            "reviewed": False,
            "issue_snapshot": None,
            "actions_applied": actions_applied,
            "comment_added": False,
            "assignee_updated": None,
            "transitioned_to": None,
            "updated_fields": {},
            "success": False,
            "error": read_outcome.get("error"),
            "runtime_events": runtime_events,
        }

    issue_snapshot = read_outcome.get("result")

    async def _apply(action_name: str, action_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        outcome = await execute_jira_workflow_action(action_name, action_kwargs)
        actions_applied.append(
            {
                "action": action_name,
                "success": bool(outcome.get("success")),
                "error": outcome.get("error"),
            }
        )
        return outcome

    review_comment = data.get("review_comment")
    comment_added = False
    if isinstance(review_comment, str) and review_comment.strip():
        comment_outcome = await _apply("add_comment", {"issue_key": issue_key, "comment": review_comment.strip()})
        if not comment_outcome.get("success"):
            runtime_events.append(_event("task.jira_workflow_review.failed", "failed", issue_key, {"error": comment_outcome.get("error")}))
            return {
                "issue_key": issue_key,
                "reviewed": True,
                "issue_snapshot": issue_snapshot,
                "actions_applied": actions_applied,
                "comment_added": False,
                "assignee_updated": None,
                "transitioned_to": None,
                "updated_fields": data.get("fields") if isinstance(data.get("fields"), dict) else {},
                "success": False,
                "error": comment_outcome.get("error"),
                "runtime_events": runtime_events,
            }
        comment_added = True

    assignee_updated = None
    assignee = data.get("assignee")
    if isinstance(assignee, str) and assignee.strip():
        assign_outcome = await _apply("assign_issue", {"issue_key": issue_key, "assignee": assignee.strip()})
        if not assign_outcome.get("success"):
            runtime_events.append(_event("task.jira_workflow_review.failed", "failed", issue_key, {"error": assign_outcome.get("error")}))
            return {
                "issue_key": issue_key,
                "reviewed": True,
                "issue_snapshot": issue_snapshot,
                "actions_applied": actions_applied,
                "comment_added": comment_added,
                "assignee_updated": None,
                "transitioned_to": None,
                "updated_fields": data.get("fields") if isinstance(data.get("fields"), dict) else {},
                "success": False,
                "error": assign_outcome.get("error"),
                "runtime_events": runtime_events,
            }
        assignee_updated = assignee.strip()

    updated_fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    if updated_fields:
        update_outcome = await _apply("update_issue", {"issue_key": issue_key, "fields": updated_fields})
        if not update_outcome.get("success"):
            runtime_events.append(_event("task.jira_workflow_review.failed", "failed", issue_key, {"error": update_outcome.get("error")}))
            return {
                "issue_key": issue_key,
                "reviewed": True,
                "issue_snapshot": issue_snapshot,
                "actions_applied": actions_applied,
                "comment_added": comment_added,
                "assignee_updated": assignee_updated,
                "transitioned_to": None,
                "updated_fields": updated_fields,
                "success": False,
                "error": update_outcome.get("error"),
                "runtime_events": runtime_events,
            }

    transitioned_to = None
    transition = data.get("transition")
    if isinstance(transition, str) and transition.strip():
        transition_outcome = await _apply(
            "transition_issue",
            {
                "issue_key": issue_key,
                "transition": transition.strip(),
                "comment": data.get("transition_comment"),
            },
        )
        if not transition_outcome.get("success"):
            runtime_events.append(_event("task.jira_workflow_review.failed", "failed", issue_key, {"error": transition_outcome.get("error")}))
            return {
                "issue_key": issue_key,
                "reviewed": True,
                "issue_snapshot": issue_snapshot,
                "actions_applied": actions_applied,
                "comment_added": comment_added,
                "assignee_updated": assignee_updated,
                "transitioned_to": None,
                "updated_fields": updated_fields,
                "success": False,
                "error": transition_outcome.get("error"),
                "runtime_events": runtime_events,
            }
        transitioned_to = transition.strip()

    runtime_events.append(
        _event(
            "task.jira_workflow_review.completed",
            "completed",
            issue_key,
            {
                "success": True,
                "actions_applied": len(actions_applied),
                "comment_added": comment_added,
                "assignee_updated": assignee_updated,
                "transitioned_to": transitioned_to,
            },
        )
    )

    return {
        "issue_key": issue_key,
        "reviewed": True,
        "issue_snapshot": issue_snapshot,
        "actions_applied": actions_applied,
        "comment_added": comment_added,
        "assignee_updated": assignee_updated,
        "transitioned_to": transitioned_to,
        "updated_fields": updated_fields,
        "success": True,
        "error": None,
        "runtime_events": runtime_events,
    }
