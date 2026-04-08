"""Skill-first GitHub review task workflow."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.agents.executor import execute_skill
from src.runtime.adapter_executor import execute_adapter_action
from src.runtime.events import build_runtime_event


def _event(event_type: str, state: str, detail_payload: Dict[str, Any]) -> Dict[str, Any]:
    return build_runtime_event(
        event_type=event_type,
        execution_type="task",
        state=state,
        session_id=None,
        request_id=None,
        agent_id=None,
        summary="github review task",
        detail_payload=detail_payload,
        legacy_payload={"legacy_type": event_type.replace(".", "_")},
    )


def _normalize_review_summary(skill_output: Any, skill_data: Dict[str, Any], fallback_comment: Optional[str]) -> str:
    if isinstance(skill_data.get("review_summary"), str) and skill_data.get("review_summary").strip():
        return skill_data["review_summary"].strip()
    if isinstance(skill_data.get("summary"), str) and skill_data.get("summary").strip():
        return skill_data["summary"].strip()
    if isinstance(skill_output, str) and skill_output.strip():
        return skill_output.strip()
    if isinstance(fallback_comment, str) and fallback_comment.strip():
        return fallback_comment.strip()
    return ""


def _normalize_review_writeback(skill_output: Any, skill_data: Dict[str, Any], fallback_comment: Optional[str]) -> tuple[str, str | None]:
    review_body = _normalize_review_summary(skill_output, skill_data, fallback_comment)

    raw_event = (
        skill_data.get("review_event")
        or skill_data.get("event")
    )
    if isinstance(raw_event, str) and raw_event.strip():
        event = raw_event.strip().upper()
        if event in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}:
            return event, review_body or None

    decision = str(skill_data.get("decision") or "").strip().lower()
    if decision in {"approved", "approve"}:
        return "APPROVE", review_body or None
    if decision in {"rejected", "changes_requested", "request_changes"}:
        return "REQUEST_CHANGES", review_body or None

    if "approved" in skill_data and isinstance(skill_data.get("approved"), bool):
        return ("APPROVE" if skill_data.get("approved") else "REQUEST_CHANGES"), review_body or None

    if "request_changes" in skill_data and isinstance(skill_data.get("request_changes"), bool) and skill_data.get("request_changes"):
        return "REQUEST_CHANGES", review_body or None

    return "COMMENT", review_body or None


async def run_github_review_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    owner = str(payload.get("owner") or "").strip()
    repo = str(payload.get("repo") or "").strip()
    pull_number = payload.get("pull_number")
    if not owner or not repo or pull_number is None:
        return {
            "success": False,
            "error": "owner, repo, and pull_number are required",
            "review_summary": None,
            "runtime_events": [_event("task.github_review.failed", "failed", {"error": "missing_required_fields"})],
            "secondary_action_attempted": False,
            "secondary_action_success": False,
            "actions_applied": [],
            "result": {},
            "skill_name": str(payload.get("skill_name") or "review-pull-request"),
        }

    skill_name = str(payload.get("skill_name") or "review-pull-request").strip() or "review-pull-request"
    skill_kwargs = dict(payload.get("skill_kwargs") or {})
    review_comment_input = payload.get("comment")
    review_metadata = payload.get("metadata")

    skill_result = await execute_skill(
        skill_name,
        _use_execution_bus=True,
        owner=owner,
        repo=repo,
        pull_number=pull_number,
        metadata=review_metadata,
        **skill_kwargs,
    )

    skill_success = bool(getattr(skill_result, "success", False))
    skill_error = getattr(skill_result, "error", None)
    skill_output = getattr(skill_result, "output", "")
    skill_data = getattr(skill_result, "data", {}) if isinstance(getattr(skill_result, "data", None), dict) else {}

    runtime_events = [_event("task.github_review.skill.completed" if skill_success else "task.github_review.skill.failed", "completed" if skill_success else "failed", {
        "skill_name": skill_name,
        "success": skill_success,
        "error": skill_error,
    })]

    review_event, review_summary = _normalize_review_writeback(skill_output, skill_data, review_comment_input)
    writeback_mode = str(payload.get("writeback_mode") or "").strip().lower()
    secondary_action_id = "adapter:github:add_comment" if writeback_mode == "issue_comment" else "adapter:github:review_pull_request"
    action_gate = payload.get("_action_gate") if callable(payload.get("_action_gate")) else None

    secondary_action_attempted = False
    secondary_action_success = False
    actions_applied = []
    error_value = skill_error

    if skill_success and review_summary:
        secondary_action_attempted = True
        action_payload = {
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
            "comment": review_summary,
        }
        if secondary_action_id == "adapter:github:review_pull_request":
            action_payload["review_event"] = review_event
        gate = action_gate(secondary_action_id, action_payload) if action_gate else None
        if gate is not None:
            error_value = "capability policy blocked for secondary action"
            runtime_events.append(
                _event(
                    "task.github_review.secondary_action.blocked",
                    "blocked",
                    {
                        "secondary_action_id": secondary_action_id,
                        "reason": gate.get("reason"),
                        "message": gate.get("message"),
                    },
                )
            )
        else:
            add_comment_result = await execute_adapter_action(
                secondary_action_id,
                action_payload,
            )
            secondary_action_success = bool(add_comment_result.get("success"))
            actions_applied.append({"action_id": secondary_action_id, "success": secondary_action_success, "error": add_comment_result.get("error")})
            runtime_events.extend(add_comment_result.get("runtime_events") or [])
            runtime_events.append(
                _event(
                    "task.github_review.secondary_action.completed" if secondary_action_success else "task.github_review.secondary_action.failed",
                    "completed" if secondary_action_success else "failed",
                    {
                        "secondary_action_id": secondary_action_id,
                        "success": secondary_action_success,
                        "error": add_comment_result.get("error"),
                    },
                )
            )
            if not secondary_action_success:
                error_value = add_comment_result.get("error") or "Failed to write GitHub review comment"

    if skill_success and not review_summary:
        error_value = "Review succeeded but no summary/comment text available for write-back"

    success = skill_success and secondary_action_success and not error_value
    runtime_events.append(
        _event(
            "task.github_review.completed" if success else "task.github_review.failed",
            "completed" if success else "failed",
            {
                "owner": owner,
                "repo": repo,
                "pull_number": pull_number,
                "skill_name": skill_name,
                "review_event": review_event,
                "success": success,
                "error": error_value,
            },
        )
    )

    return {
        "task_type": "github_review_task",
        "success": success,
        "error": error_value,
        "owner": owner,
        "repo": repo,
        "pull_number": pull_number,
        "review_summary": review_summary or None,
        "review_event": review_event,
        "review_written": secondary_action_success,
        "comment_written": secondary_action_success,
        "secondary_action_attempted": secondary_action_attempted,
        "secondary_action_success": secondary_action_success,
        "secondary_action_id": secondary_action_id,
        "actions_applied": actions_applied,
        "runtime_events": runtime_events,
        "result": {
            "skill": {
                "name": skill_name,
                "success": skill_success,
                "output": skill_output,
                "error": skill_error,
                "data": skill_data,
            }
        },
        "skill_name": skill_name,
    }
