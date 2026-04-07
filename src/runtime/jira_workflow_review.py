"""Skill-backed Jira workflow-aware runtime task orchestration."""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.executor import run_skill_execution
from src.runtime.adapter_executor import execute_jira_workflow_action
from src.runtime.jira_workflow_contract import (
    JiraWorkflowReviewOutcome,
    derive_workflow_actions_from_outcome,
    normalize_skill_review_outcome,
    normalize_workflow_review_payload,
)
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
    plan = normalize_workflow_review_payload(payload)
    action_gate = payload.get("_action_gate") if callable(payload.get("_action_gate")) else None
    if not plan.issue_key:
        return _build_failure(
            issue_key=None,
            error="issue_key is required",
            actions_applied=[],
            workflow_outcome="failed",
            approved=None,
            runtime_events=[_event("task.jira_workflow_review.failed", "failed", "", {"error": "issue_key is required"})],
        )

    runtime_events: List[Dict[str, Any]] = []
    actions_applied: List[Dict[str, Any]] = []
    for warning in plan.normalization_warnings:
        runtime_events.append(
            _event(
                "recovery.warning",
                "warning",
                plan.issue_key,
                {"warning": warning},
            )
        )

    read_outcome = await execute_jira_workflow_action("read_issue", {"issue_key": plan.issue_key})
    if not read_outcome.get("success"):
        return _build_failure(
            issue_key=plan.issue_key,
            error=read_outcome.get("error") or "failed_to_read_issue",
            actions_applied=actions_applied,
            workflow_outcome="failed",
            approved=None,
            runtime_events=[_event("task.jira_workflow_review.failed", "failed", plan.issue_key, {"error": read_outcome.get("error")})],
        )
    issue_snapshot = read_outcome.get("result")

    outcome = await _resolve_review_outcome(plan, issue_snapshot)
    if outcome is None:
        return _build_failure(
            issue_key=plan.issue_key,
            error="No structured workflow outcome produced by review skill",
            actions_applied=actions_applied,
            workflow_outcome="failed",
            approved=None,
            runtime_events=[_event("task.jira_workflow_review.failed", "failed", plan.issue_key, {"error": "missing_structured_outcome", "skill_name": plan.skill_name})],
            issue_snapshot=issue_snapshot,
            skill_name=plan.skill_name,
        )

    action_plan = derive_workflow_actions_from_outcome(plan=plan, outcome=outcome, issue_snapshot=issue_snapshot)

    if action_plan.get("comment"):
        comment_outcome = await _apply_action(
            actions_applied,
            "add_comment",
            {"issue_key": plan.issue_key, "comment": action_plan.get("comment")},
            action_gate=action_gate,
        )
        if comment_outcome.get("blocked"):
            runtime_events.append(
                _event("task.jira_workflow_review.action.blocked", "blocked", plan.issue_key, {"action": "add_comment", "error": comment_outcome.get("error")})
            )
        elif not comment_outcome.get("success"):
            return _build_failure(
                issue_key=plan.issue_key,
                error=comment_outcome.get("error") or "comment_failed",
                actions_applied=actions_applied,
                workflow_outcome=action_plan.get("workflow_outcome", "failed"),
                approved=action_plan.get("approved"),
                runtime_events=[_event("task.jira_workflow_review.failed", "failed", plan.issue_key, {"error": comment_outcome.get("error")})],
                issue_snapshot=issue_snapshot,
                skill_name=plan.skill_name,
                reassignment_target=action_plan.get("reassignment_target"),
            )

    if action_plan.get("fields"):
        update_outcome = await _apply_action(
            actions_applied,
            "update_issue",
            {"issue_key": plan.issue_key, "fields": action_plan.get("fields")},
            action_gate=action_gate,
        )
        if update_outcome.get("blocked"):
            runtime_events.append(
                _event("task.jira_workflow_review.action.blocked", "blocked", plan.issue_key, {"action": "update_issue", "error": update_outcome.get("error")})
            )
        elif not update_outcome.get("success"):
            return _build_failure(
                issue_key=plan.issue_key,
                error=update_outcome.get("error") or "update_failed",
                actions_applied=actions_applied,
                workflow_outcome=action_plan.get("workflow_outcome", "failed"),
                approved=action_plan.get("approved"),
                runtime_events=[_event("task.jira_workflow_review.failed", "failed", plan.issue_key, {"error": update_outcome.get("error")})],
                issue_snapshot=issue_snapshot,
                skill_name=plan.skill_name,
                reassignment_target=action_plan.get("reassignment_target"),
            )

    if action_plan.get("transition"):
        transition_outcome = await _apply_action(
            actions_applied,
            "transition_issue",
            {
                "issue_key": plan.issue_key,
                "transition": action_plan.get("transition"),
                "comment": action_plan.get("transition_comment"),
            },
            action_gate=action_gate,
        )
        if not transition_outcome.get("success"):
            return _build_failure(
                issue_key=plan.issue_key,
                error=transition_outcome.get("error") or "transition_failed",
                actions_applied=actions_applied,
                workflow_outcome=action_plan.get("workflow_outcome", "failed"),
                approved=action_plan.get("approved"),
                runtime_events=[_event("task.jira_workflow_review.failed", "failed", plan.issue_key, {"error": transition_outcome.get("error")})],
                issue_snapshot=issue_snapshot,
                skill_name=plan.skill_name,
                reassignment_target=action_plan.get("reassignment_target"),
            )

    if action_plan.get("assignee"):
        assign_outcome = await _apply_action(
            actions_applied,
            "assign_issue",
            {"issue_key": plan.issue_key, "assignee": action_plan.get("assignee")},
            action_gate=action_gate,
        )
        if not assign_outcome.get("success"):
            return _build_failure(
                issue_key=plan.issue_key,
                error=assign_outcome.get("error") or "assignment_failed",
                actions_applied=actions_applied,
                workflow_outcome=action_plan.get("workflow_outcome", "failed"),
                approved=action_plan.get("approved"),
                runtime_events=[_event("task.jira_workflow_review.failed", "failed", plan.issue_key, {"error": assign_outcome.get("error")})],
                issue_snapshot=issue_snapshot,
                skill_name=plan.skill_name,
                reassignment_target=action_plan.get("reassignment_target"),
            )

    if action_plan.get("reassignment_warning"):
        runtime_events.append(
            _event(
                "recovery.warning",
                "warning",
                plan.issue_key,
                {"warning": action_plan.get("reassignment_warning")},
            )
        )

    runtime_events.append(
        _event(
            "task.jira_workflow_review.completed",
            "completed",
            plan.issue_key,
            {
                "skill_name": plan.skill_name,
                "workflow_outcome": action_plan.get("workflow_outcome"),
                "approved": action_plan.get("approved"),
                "success_transition": plan.success_transition,
                "failure_transition": plan.failure_transition,
                "reassignment_target": action_plan.get("reassignment_target"),
                "actions_applied": len(actions_applied),
            },
        )
    )

    return {
        "issue_key": plan.issue_key,
        "reviewed": True,
        "issue_snapshot": issue_snapshot,
        "actions_applied": actions_applied,
        "comment_added": bool(action_plan.get("comment")),
        "assignee_updated": action_plan.get("assignee"),
        "transitioned_to": action_plan.get("transition"),
        "updated_fields": action_plan.get("fields") or {},
        "workflow_outcome": action_plan.get("workflow_outcome"),
        "approved": action_plan.get("approved"),
        "skill_name": plan.skill_name,
        "success": True,
        "error": None,
        "runtime_events": runtime_events,
    }


async def _resolve_review_outcome(plan, issue_snapshot: Any) -> JiraWorkflowReviewOutcome | None:
    if not plan.skill_name:
        # Backward-compatible direct payload path.
        return JiraWorkflowReviewOutcome(
            approved=True,
            outcome_type="approved",
            summary="direct_payload_path",
            comment=plan.review_comment_template,
            data={"source": "direct_payload"},
        )

    skill_kwargs = {
        **dict(plan.skill_kwargs or {}),
        "issue_key": plan.issue_key,
        "issue_snapshot": issue_snapshot,
        "workflow_context": dict(plan.workflow_context or {}),
    }
    skill_result = await run_skill_execution(plan.skill_name, **skill_kwargs)
    if not getattr(skill_result, "success", False):
        return JiraWorkflowReviewOutcome(
            approved=False,
            outcome_type="rejected",
            summary=getattr(skill_result, "error", None),
            comment=getattr(skill_result, "error", None),
            data=getattr(skill_result, "data", {}) if isinstance(getattr(skill_result, "data", None), dict) else {},
        )
    return normalize_skill_review_outcome(skill_result)


async def _apply_action(
    actions_applied: List[Dict[str, Any]],
    action_name: str,
    kwargs: Dict[str, Any],
    *,
    action_gate: Any = None,
) -> Dict[str, Any]:
    if callable(action_gate):
        gate_outcome = action_gate(action_name, kwargs)
        if isinstance(gate_outcome, dict) and gate_outcome.get("blocked"):
            outcome = {
                "success": False,
                "error": gate_outcome.get("error") or f"action_blocked:{action_name}",
                "blocked": True,
                "blocked_reason": gate_outcome.get("reason"),
            }
            actions_applied.append(
                {
                    "action": action_name,
                    "success": False,
                    "error": outcome.get("error"),
                    "blocked": True,
                }
            )
            return outcome
    outcome = await execute_jira_workflow_action(action_name, kwargs)
    actions_applied.append(
        {
            "action": action_name,
            "success": bool(outcome.get("success")),
            "error": outcome.get("error"),
            "blocked": bool(outcome.get("blocked")),
        }
    )
    return outcome


def _build_failure(
    *,
    issue_key: Any,
    error: str,
    actions_applied: List[Dict[str, Any]],
    workflow_outcome: str,
    approved: Any,
    runtime_events: List[Dict[str, Any]],
    issue_snapshot: Any = None,
    skill_name: Any = None,
    reassignment_target: Any = None,
) -> Dict[str, Any]:
    return {
        "issue_key": issue_key,
        "reviewed": bool(issue_snapshot is not None),
        "issue_snapshot": issue_snapshot,
        "actions_applied": actions_applied,
        "comment_added": False,
        "assignee_updated": None,
        "transitioned_to": None,
        "updated_fields": {},
        "workflow_outcome": workflow_outcome,
        "approved": approved,
        "skill_name": skill_name,
        "reassignment_target": reassignment_target,
        "success": False,
        "error": error,
        "runtime_events": runtime_events,
    }
