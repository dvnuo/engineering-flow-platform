"""Structured Jira workflow review contract helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class JiraWorkflowReviewPlan:
    issue_key: str
    skill_name: Optional[str] = None
    skill_kwargs: Dict[str, Any] = field(default_factory=dict)
    success_transition: Optional[str] = None
    failure_transition: Optional[str] = None
    success_reassign_to: Optional[str] = None
    failure_reassign_to: Optional[str] = None
    explicit_success_assignee: Optional[str] = None
    explicit_failure_assignee: Optional[str] = None
    review_comment_template: Optional[str] = None
    transition_comment_template: Optional[str] = None
    fields_on_success: Dict[str, Any] = field(default_factory=dict)
    fields_on_failure: Dict[str, Any] = field(default_factory=dict)
    workflow_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JiraWorkflowReviewOutcome:
    approved: Optional[bool]
    outcome_type: str
    summary: Optional[str] = None
    comment: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


def normalize_workflow_review_payload(payload: Dict[str, Any]) -> JiraWorkflowReviewPlan:
    data = dict(payload or {})
    issue_key = str(data.get("issue_key") or "").strip()
    return JiraWorkflowReviewPlan(
        issue_key=issue_key,
        skill_name=_clean_optional_str(data.get("skill_name")),
        skill_kwargs=dict(data.get("skill_kwargs") or {}),
        success_transition=_clean_optional_str(data.get("success_transition") or data.get("transition")),
        failure_transition=_clean_optional_str(data.get("failure_transition")),
        success_reassign_to=_clean_optional_str(data.get("success_reassign_to")),
        failure_reassign_to=_clean_optional_str(data.get("failure_reassign_to")),
        explicit_success_assignee=_clean_optional_str(data.get("explicit_success_assignee") or data.get("assignee")),
        explicit_failure_assignee=_clean_optional_str(data.get("explicit_failure_assignee")),
        review_comment_template=_clean_optional_str(data.get("review_comment_template") or data.get("review_comment")),
        transition_comment_template=_clean_optional_str(data.get("transition_comment_template") or data.get("transition_comment")),
        fields_on_success=dict(data.get("fields_on_success") or data.get("fields") or {}),
        fields_on_failure=dict(data.get("fields_on_failure") or {}),
        workflow_context=dict(data.get("workflow_context") or {}),
    )


def normalize_skill_review_outcome(skill_result: Any) -> Optional[JiraWorkflowReviewOutcome]:
    data = getattr(skill_result, "data", None)
    if not isinstance(data, dict):
        return None

    workflow_outcome = data.get("workflow_outcome")
    if isinstance(workflow_outcome, dict):
        outcome_type = _normalize_outcome_type(workflow_outcome.get("outcome_type") or workflow_outcome.get("decision"))
        approved = _normalize_approved(workflow_outcome.get("approved"))
        if outcome_type is None and approved is not None:
            outcome_type = "approved" if approved else "rejected"
        if outcome_type is None:
            return None
        return JiraWorkflowReviewOutcome(
            approved=approved,
            outcome_type=outcome_type,
            summary=_clean_optional_str(workflow_outcome.get("summary")),
            comment=_clean_optional_str(workflow_outcome.get("comment")),
            data=dict(workflow_outcome),
        )

    approved = _normalize_approved(data.get("approved"))
    if approved is None:
        approved = _normalize_approved(data.get("passed"))
    decision = _normalize_outcome_type(data.get("decision"))
    if decision is None and approved is not None:
        decision = "approved" if approved else "rejected"
    if decision is None:
        return None
    return JiraWorkflowReviewOutcome(
        approved=approved,
        outcome_type=decision,
        summary=_clean_optional_str(data.get("summary") or getattr(skill_result, "output", None)),
        comment=_clean_optional_str(data.get("comment")),
        data=dict(data),
    )


def derive_workflow_actions_from_outcome(
    *,
    plan: JiraWorkflowReviewPlan,
    outcome: JiraWorkflowReviewOutcome,
    issue_snapshot: Any,
) -> Dict[str, Any]:
    approved = outcome.outcome_type == "approved"
    comment = outcome.comment or plan.review_comment_template
    if comment and "{summary}" in comment:
        comment = comment.format(summary=outcome.summary or "")

    fields = plan.fields_on_success if approved else plan.fields_on_failure
    transition = plan.success_transition if approved else plan.failure_transition

    explicit_assignee = plan.explicit_success_assignee if approved else plan.explicit_failure_assignee
    reassign_target = plan.success_reassign_to if approved else plan.failure_reassign_to
    assignee, warning = resolve_reassignment_target(
        issue_snapshot=issue_snapshot,
        target=reassign_target,
        explicit_assignee=explicit_assignee,
    )

    return {
        "approved": approved,
        "workflow_outcome": outcome.outcome_type,
        "comment": comment,
        "fields": dict(fields or {}),
        "transition": transition,
        "transition_comment": plan.transition_comment_template,
        "assignee": assignee,
        "reassignment_target": reassign_target,
        "reassignment_warning": warning,
    }


def resolve_reassignment_target(*, issue_snapshot: Any, target: Optional[str], explicit_assignee: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    resolved_target = _clean_optional_str(target)
    if resolved_target in {None, "explicit"}:
        return _clean_optional_str(explicit_assignee), None

    if resolved_target == "reporter":
        reporter = _extract_issue_user(issue_snapshot, "reporter")
        if reporter:
            return reporter, None
        return None, "reporter_not_available"

    if resolved_target == "requester":
        requester = _extract_issue_user(issue_snapshot, "requester")
        if requester:
            return requester, None
        return None, "requester_not_available"

    return None, f"unsupported_reassign_target:{resolved_target}"


def _extract_issue_user(issue_snapshot: Any, role: str) -> Optional[str]:
    if not isinstance(issue_snapshot, dict):
        return None

    fields = issue_snapshot.get("fields") if isinstance(issue_snapshot.get("fields"), dict) else issue_snapshot
    if role == "reporter":
        candidate = fields.get("reporter")
    else:
        candidate = fields.get("requester") or fields.get("customfield_requester")

    if isinstance(candidate, dict):
        for key in ("accountId", "name", "emailAddress", "displayName"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return None


def _normalize_outcome_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in {"approved", "approve", "pass", "passed", "success"}:
        return "approved"
    if text in {"rejected", "reject", "needs_changes", "changes_requested", "failed", "fail"}:
        return "rejected"
    return None


def _normalize_approved(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "approved", "pass", "passed", "success"}:
            return True
        if lowered in {"false", "no", "rejected", "fail", "failed", "needs_changes"}:
            return False
    return None


def _clean_optional_str(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
