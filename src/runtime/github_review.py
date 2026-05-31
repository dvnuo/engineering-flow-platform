"""GitHub review task workflow backed by chat/tool-loop skill execution."""

from __future__ import annotations

import json
import hashlib
from typing import Any, Callable, Dict, Optional
import logging

from src.github.api import github_channel
from src.runtime.events import build_runtime_event
from src.runtime.runtime_adapter_execution import execute_adapter_action_via_bus

logger = logging.getLogger(__name__)

_READ_ONLY_GITHUB_REVIEW_TOOL_CAPABILITY_IDS = [
    "tool:github_get_pr",
    "tool:github_get_pr_files",
    "tool:github_get_pr_file_patch",
    "tool:github_get_pr_diff",
    "tool:github_get_pr_comments",
    "tool:github_list_pr_reviews",
]
_READ_ONLY_GITHUB_REVIEW_CAPABILITY_TYPES = ["tool"]


def _resolve_github_review_chat_session_id(payload: Dict[str, Any], owner: str, repo: str, pull_number: int) -> str:
    for key in ("chat_session_id", "session_id", "_runtime_session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = payload.get("_execution_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    for key in ("task_id", "portal_task_id", "current_task_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return f"github-review-task:{value.strip()}"
    dedupe_key = payload.get("dedupe_key")
    if isinstance(dedupe_key, str) and dedupe_key.strip():
        digest = hashlib.sha256(dedupe_key.strip().encode("utf-8")).hexdigest()[:16]
        return f"github-review:{owner}:{repo}:{pull_number}:{digest}"
    return f"github-review:{owner}:{repo}:{pull_number}"


def _build_github_review_chat_prompt(
    *,
    skill_name: str,
    owner: str,
    repo: str,
    pull_number: int,
    requested_head_sha: str | None,
    review_target: Dict[str, Any] | None,
    requested_event: str | None,
    writeback_mode: str | None,
    runtime_managed_writeback: bool = True,
) -> str:
    target_text = json.dumps(review_target or {}, ensure_ascii=False, sort_keys=True)
    requested_event_text = requested_event or "COMMENT"
    writeback_mode_text = writeback_mode or "review_pull_request"
    return (
        f"/skill use {skill_name}\n\n"
        "You are running an automated GitHub pull request review task through the normal chat/tool loop.\n"
        "Use the active skill instructions and GitHub tools to inspect the pull request.\n\n"
        f"Repository: {owner}/{repo}\n"
        f"Pull request: #{pull_number}\n"
        f"Expected head SHA: {requested_head_sha or '(not provided)'}\n"
        f"Review target: {target_text}\n"
        f"Requested review event default: {requested_event_text}\n"
        f"Runtime writeback mode: {writeback_mode_text}\n"
        f"Runtime managed writeback: {str(runtime_managed_writeback).lower()}\n\n"
        "Required behavior:\n"
        "1. Fetch PR metadata with github_get_pr.\n"
        "2. Fetch changed files with github_get_pr_files.\n"
        "3. Inspect relevant file patches with github_get_pr_file_patch; use github_get_pr_diff only if necessary.\n"
        "4. Check existing PR comments and reviews before raising findings.\n"
        "5. Return concise markdown review content with Pull Request Summary and Findings.\n\n"
        "Important automation constraint:\n"
        "- Do NOT call github_add_comment in this chat turn.\n"
        "- Do NOT call github_add_pr_review_comment in this chat turn.\n"
        "- The runtime task wrapper will perform freshness guard, governance, and GitHub writeback after your final answer.\n"
        "- Your final assistant response must be the review body to write back.\n"
    )


def _extract_review_text_from_chat_result(output_payload: Dict[str, Any]) -> str:
    candidates = [output_payload.get("response"), output_payload.get("output"), output_payload.get("content"), output_payload.get("message")]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    data = output_payload.get("data")
    if isinstance(data, dict):
        for key in ("response", "output", "content", "review_summary", "summary"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _event_payload(event: Any) -> Dict[str, Any]:
    if not isinstance(event, dict):
        return {}
    payload = event.get("detail_payload")
    if isinstance(payload, dict):
        return payload
    payload = event.get("data")
    if isinstance(payload, dict):
        return payload
    return event


def _event_type(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    return str(event.get("event_type") or event.get("event") or event.get("type") or "").strip()


def _chat_result_confirms_skill_activation(events: list[Any], skill_name: str) -> bool:
    expected = str(skill_name or "").strip()
    if not expected:
        return False
    for event in events:
        event_type = _event_type(event)
        payload = _event_payload(event)
        candidate = str(payload.get("skill") or payload.get("skill_name") or payload.get("active_skill") or "").strip()
        if candidate == expected and event_type in {"skill_matched", "skill_runtime_applied", "skill_contract_active"}:
            return True
    return False


def _chat_result_has_skill_not_found(output_payload: Dict[str, Any], events: list[Any], skill_name: str) -> bool:
    expected = str(skill_name or "").strip()
    texts = []
    for key in ("response", "output", "content", "message", "error"):
        value = output_payload.get(key)
        if isinstance(value, str):
            texts.append(value)
    for text in texts:
        lowered = text.lower()
        if "skill not found" in lowered and expected.lower() in lowered:
            return True
    for event in events:
        payload = _event_payload(event)
        reason = str(payload.get("reason") or "").strip().lower()
        candidate = str(payload.get("skill") or payload.get("skill_name") or "").strip()
        if reason == "skill_not_found" and candidate == expected:
            return True
    return False


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


def _automation_trace_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    source = str(payload.get("source") or "").strip() or None
    rule_id = str(payload.get("rule_id") or "").strip() or None
    automation_rule_id = str(payload.get("automation_rule_id") or payload.get("rule_id") or "").strip() or None
    dedupe_key = str(payload.get("dedupe_key") or "").strip() or None
    review_target = payload.get("review_target") if isinstance(payload.get("review_target"), dict) else None
    trace = {
        "source": source,
        "rule_id": rule_id,
        "automation_rule_id": automation_rule_id,
        "dedupe_key": dedupe_key,
        "review_target": review_target,
    }
    return {key: value for key, value in trace.items() if value is not None}


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


def _normalize_skill_kwargs(raw_value: Any) -> tuple[Dict[str, Any] | None, str | None, str | None]:
    if raw_value is None:
        return {}, None, None
    if isinstance(raw_value, dict):
        return dict(raw_value), None, None
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return None, "invalid_skill_kwargs_json", "skill_kwargs must be valid JSON when provided as a string"
        if not isinstance(parsed, dict):
            return None, "invalid_skill_kwargs_type", "skill_kwargs JSON string must decode to an object"
        return dict(parsed), None, None
    return None, "invalid_skill_kwargs_type", "skill_kwargs must be a dict or JSON object string"


def _normalize_requested_review_event(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in {"COMMENT", "APPROVE", "REQUEST_CHANGES"} else None


def _normalize_review_writeback(
    skill_output: Any,
    skill_data: Dict[str, Any],
    fallback_comment: Optional[str],
    requested_event: str | None = None,
) -> tuple[str, str | None]:
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

    return (requested_event or "COMMENT"), review_body or None


async def _get_current_pr_head_sha(owner: str, repo: str, pull_number: int) -> str | None:
    pr_payload = await github_channel.get_pull_request(owner, repo, pull_number)
    head = pr_payload.get("head") if isinstance(pr_payload, dict) else {}
    sha = head.get("sha") if isinstance(head, dict) else None
    if isinstance(sha, str) and sha.strip():
        return sha.strip()
    return None


def _metadata_has_identity_binding(metadata: Dict[str, Any]) -> bool:
    nested = metadata.get("identity_binding")
    if isinstance(nested, dict):
        system_type = str(nested.get("system_type") or "").strip()
        binding_id = str(nested.get("id") or nested.get("identity_binding_id") or "").strip()
        external_account_id = str(nested.get("external_account_id") or "").strip()
        if system_type and (binding_id or external_account_id):
            return True
    system_type = str(metadata.get("identity_binding_system_type") or "").strip()
    binding_id = str(metadata.get("identity_binding_id") or "").strip()
    external_account_id = str(metadata.get("identity_binding_external_account_id") or "").strip()
    return bool(system_type and (binding_id or external_account_id))


def _safe_identity_fragment(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    cleaned = []
    for ch in text:
        cleaned.append(ch if ch.isalnum() or ch in {"-", "_", ".", ":"} else "-")
    result = "".join(cleaned).strip("-")
    return result[:120] or fallback


def _ensure_github_writeback_identity_binding(
    metadata: Dict[str, Any],
    *,
    payload: Dict[str, Any],
    action_id: str,
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    enriched = dict(metadata or {})
    if action_id not in {"adapter:github:review_pull_request", "adapter:github:add_comment"}:
        return enriched
    if _metadata_has_identity_binding(enriched):
        return enriched
    owner = kwargs.get("owner") or payload.get("owner")
    repo = kwargs.get("repo") or payload.get("repo")
    pull_number = kwargs.get("pull_number") or payload.get("pull_number")
    task_ref = (
        enriched.get("portal_task_id")
        or enriched.get("task_id")
        or payload.get("automation_rule_id")
        or payload.get("rule_id")
        or payload.get("dedupe_key")
        or f"{owner}/{repo}#{pull_number}"
        or "unknown"
    )
    binding_id = f"runtime-github-review:{_safe_identity_fragment(task_ref, 'unknown')}"
    external_account_id = (
        enriched.get("github_identity_external_account_id")
        or enriched.get("github_actor")
        or enriched.get("github_login")
        or payload.get("identity_binding_external_account_id")
        or payload.get("_runtime_agent_id")
        or payload.get("agent_id")
        or enriched.get("agent_id")
        or "runtime-github-integration"
    )
    enriched["identity_binding"] = {
        "system_type": "github",
        "id": binding_id,
        "external_account_id": str(external_account_id),
    }
    enriched["identity_binding_source"] = "github_review_task"
    enriched["identity_binding_runtime_managed"] = True
    return enriched


async def execute_github_review_action(action_id: str, kwargs: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    execution_metadata = payload.get("_execution_metadata")
    base_metadata = dict(execution_metadata) if isinstance(execution_metadata, dict) else {}
    metadata = _ensure_github_writeback_identity_binding(
        base_metadata,
        payload=payload,
        action_id=action_id,
        kwargs=kwargs,
    )
    session_id = payload.get("session_id") or payload.get("_runtime_session_id") or metadata.get("session_id") or metadata.get("chat_session_id")
    agent_id = payload.get("agent_id") or payload.get("_runtime_agent_id") or metadata.get("agent_id") or metadata.get("chat_agent_id")
    policy_profile_id = payload.get("policy_profile_id")
    if policy_profile_id is None:
        policy_profile_id = metadata.get("policy_profile_id")
    return await execute_adapter_action_via_bus(
        action_id,
        kwargs,
        source_type="runtime",
        source_ref="github_review",
        session_id=session_id,
        agent_id=agent_id,
        policy_profile_id=policy_profile_id,
        metadata=metadata,
    )


async def _execute_review_skill_via_chat_loop(
    *,
    payload: Dict[str, Any],
    owner: str,
    repo: str,
    pull_number: int,
    skill_name: str,
    requested_event: str | None,
    requested_head_sha: str | None,
    review_target: Dict[str, Any] | None,
    review_metadata: Any,
    skill_kwargs: Dict[str, Any],
    automation_trace: Dict[str, Any],
) -> Dict[str, Any]:
    from src.config import DEFAULT_LLM_MODEL, config
    from src.efp_runtime.session.gateway_facade import runtime_session_manager
    from src.gateway.runtime_chat import run_runtime_chat
    metadata = payload.get("_execution_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    agent_id = payload.get("_runtime_agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        agent_id = metadata.get("agent_id")
    agent_id = str(agent_id).strip() if agent_id else None
    chat_session_id = _resolve_github_review_chat_session_id(payload, owner, repo, pull_number)
    chat_request_id = str(payload.get("_runtime_request_id") or metadata.get("task_id") or "").strip()
    chat_request_id = f"{chat_request_id}:chat" if chat_request_id else f"github-review-chat:{owner}:{repo}:{pull_number}"
    requested_event_text = requested_event or "COMMENT"
    writeback_mode = str(payload.get("writeback_mode") or "").strip() or None
    original_allowed_capability_ids = metadata.get("allowed_capability_ids")
    original_allowed_capability_types = metadata.get("allowed_capability_types")
    message = _build_github_review_chat_prompt(
        skill_name=skill_name, owner=owner, repo=repo, pull_number=pull_number, requested_head_sha=requested_head_sha,
        review_target=review_target, requested_event=requested_event_text, writeback_mode=writeback_mode, runtime_managed_writeback=True,
    )
    if not runtime_session_manager._initialized:
        await runtime_session_manager.initialize()
    model = metadata.get("resolved_model") or metadata.get("model") or config.llm.get("model", DEFAULT_LLM_MODEL)
    chat_metadata = {
        **metadata, "path": "/api/tasks/execute/github_review_task/chat", "task_type": "github_review_task", "skill_name": skill_name,
        "execution_mode": "chat_tool_loop", "runtime_managed_writeback": True, "allowed_capability_ids": list(_READ_ONLY_GITHUB_REVIEW_TOOL_CAPABILITY_IDS), "allowed_capability_types": list(_READ_ONLY_GITHUB_REVIEW_CAPABILITY_TYPES), "review_metadata": review_metadata, "skill_kwargs": skill_kwargs,
        "github_review": {"owner": owner, "repo": repo, "pull_number": pull_number, "expected_head_sha": requested_head_sha, "review_target": review_target, "requested_review_event": requested_event_text},
        **automation_trace,
    }
    if original_allowed_capability_ids is not None:
        chat_metadata["outer_allowed_capability_ids"] = original_allowed_capability_ids
    if original_allowed_capability_types is not None:
        chat_metadata["outer_allowed_capability_types"] = original_allowed_capability_types
    output_payload = await run_runtime_chat(
        request_id=chat_request_id,
        session_id=chat_session_id,
        message=message,
        user_name="GitHub PR Review Automation",
        request_path="/api/tasks/execute/github_review_task/chat",
        execution_metadata=chat_metadata,
        agent_id=agent_id,
        agent_name=metadata.get("agent_name") if isinstance(metadata.get("agent_name"), str) else None,
        model=model,
    )
    chat_status = str(output_payload.get("status") or "success")
    review_text = _extract_review_text_from_chat_result(output_payload)
    chat_runtime_events: list[dict[str, Any]] = []
    payload_runtime_events = output_payload.get("runtime_events") if isinstance(output_payload.get("runtime_events"), list) else []
    all_chat_events = [*chat_runtime_events, *payload_runtime_events]
    if _chat_result_has_skill_not_found(output_payload, all_chat_events, skill_name):
        return {"success": False, "output": "", "error": f"Chat/tool-loop review could not activate skill '{skill_name}'", "data": {"execution_mode": "runtime", "chat_session_id": chat_session_id, "chat_request_id": chat_request_id, "chat_status": chat_status, "skill_activation": "not_found"}, "runtime_events": all_chat_events}
    if not _chat_result_confirms_skill_activation(all_chat_events, skill_name):
        return {"success": False, "output": review_text, "error": f"EFP runtime review did not activate required skill '{skill_name}'", "data": {"execution_mode": "runtime", "chat_session_id": chat_session_id, "chat_request_id": chat_request_id, "chat_status": chat_status, "skill_activation": "missing_runtime_event"}, "runtime_events": all_chat_events}
    if chat_status in {"error", "blocked"}:
        error_value = output_payload.get("error") or output_payload.get("message") or f"EFP runtime review failed with status={chat_status}"
        return {"success": False, "output": review_text, "error": str(error_value), "data": {"execution_mode": "runtime", "chat_session_id": chat_session_id, "chat_request_id": chat_request_id, "chat_status": chat_status}, "runtime_events": all_chat_events}
    if not review_text:
        return {"success": False, "output": "", "error": "EFP runtime review returned empty review content", "data": {"execution_mode": "runtime", "chat_session_id": chat_session_id, "chat_request_id": chat_request_id, "chat_status": chat_status}, "runtime_events": all_chat_events}
    return {"success": True, "output": review_text, "error": None, "data": {"review_summary": review_text, "requested_review_event": requested_event_text, "execution_mode": "runtime", "chat_session_id": chat_session_id, "chat_request_id": chat_request_id, "chat_agent_id": agent_id, "chat_status": chat_status}, "runtime_events": all_chat_events}


async def run_github_review_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    source = str(payload.get("source") or "").strip() or None
    rule_id = str(payload.get("rule_id") or "").strip() or None
    automation_rule_id = str(payload.get("automation_rule_id") or payload.get("rule_id") or "").strip() or None
    dedupe_key = str(payload.get("dedupe_key") or "").strip() or None
    review_target = payload.get("review_target") if isinstance(payload.get("review_target"), dict) else None
    automation_trace = _automation_trace_payload(payload)

    owner = str(payload.get("owner") or "").strip()
    repo = str(payload.get("repo") or "").strip()
    raw_pull_number = payload.get("pull_number")
    if not owner or not repo or raw_pull_number is None:
        return {
            "success": False,
            "error": "owner, repo, and pull_number are required",
            "review_summary": None,
            "runtime_events": [_event("task.github_review.failed", "failed", {"error": "missing_required_fields", **automation_trace})],
            "secondary_action_attempted": False,
            "secondary_action_success": False,
            "actions_applied": [],
            "result": {},
            "skill_name": str(payload.get("skill_name") or "review-pull-request"),
            "source": source,
            "rule_id": rule_id,
            "automation_rule_id": automation_rule_id,
            "dedupe_key": dedupe_key,
            "review_target": review_target,
        }
    try:
        pull_number = int(raw_pull_number)
    except (TypeError, ValueError):
        return {
            "success": False,
            "error": "pull_number must be an integer",
            "error_code": "invalid_pull_number",
            "review_summary": None,
            "runtime_events": [_event("task.github_review.failed", "failed", {"error_code": "invalid_pull_number", **automation_trace})],
            "secondary_action_attempted": False,
            "secondary_action_success": False,
            "actions_applied": [],
            "result": {},
            "skill_name": str(payload.get("skill_name") or "review-pull-request"),
            "source": source,
            "rule_id": rule_id,
            "automation_rule_id": automation_rule_id,
            "dedupe_key": dedupe_key,
            "review_target": review_target,
        }
    if pull_number <= 0:
        return {
            "success": False,
            "error": "pull_number must be a positive integer",
            "error_code": "invalid_pull_number",
            "review_summary": None,
            "runtime_events": [_event("task.github_review.failed", "failed", {"error_code": "invalid_pull_number", **automation_trace})],
            "secondary_action_attempted": False,
            "secondary_action_success": False,
            "actions_applied": [],
            "result": {},
            "skill_name": str(payload.get("skill_name") or "review-pull-request"),
            "source": source,
            "rule_id": rule_id,
            "automation_rule_id": automation_rule_id,
            "dedupe_key": dedupe_key,
            "review_target": review_target,
        }

    skill_name = str(payload.get("skill_name") or "review-pull-request").strip() or "review-pull-request"
    skill_kwargs, skill_kwargs_error_code, skill_kwargs_error = _normalize_skill_kwargs(payload.get("skill_kwargs"))
    if skill_kwargs_error_code:
        return {
            "task_type": "github_review_task",
            "success": False,
            "error_code": skill_kwargs_error_code,
            "error": skill_kwargs_error,
            "review_summary": None,
            "secondary_action_attempted": False,
            "secondary_action_success": False,
            "actions_applied": [],
            "result": {},
            "skill_name": skill_name,
            "runtime_events": [
                _event(
                    "task.github_review.failed",
                    "failed",
                    {
                        "error_code": skill_kwargs_error_code,
                        "error": skill_kwargs_error,
                        "skill_name": skill_name,
                        **automation_trace,
                    },
                )
            ],
            "source": source,
            "rule_id": rule_id,
            "automation_rule_id": automation_rule_id,
            "dedupe_key": dedupe_key,
            "review_target": review_target,
        }
    review_comment_input = payload.get("comment")
    review_metadata = payload.get("metadata")
    requested_event = _normalize_requested_review_event(payload.get("review_event"))

    default_skill_kwargs: Dict[str, Any] = {}
    raw_head_sha = payload.get("head_sha")
    if isinstance(raw_head_sha, str) and raw_head_sha.strip():
        default_skill_kwargs["head_sha"] = raw_head_sha.strip()
    if isinstance(payload.get("review_target"), dict):
        default_skill_kwargs["review_target"] = dict(payload.get("review_target") or {})
    if payload.get("max_files") is not None:
        default_skill_kwargs["max_files"] = payload.get("max_files")
    if payload.get("max_diff_chars") is not None:
        default_skill_kwargs["max_diff_chars"] = payload.get("max_diff_chars")
    if requested_event is not None:
        default_skill_kwargs["review_event"] = requested_event
    resolved_skill_kwargs = {**default_skill_kwargs, **skill_kwargs}
    runtime_events: list[Dict[str, Any]] = []
    requested_head_sha = str(payload.get("head_sha") or "").strip()
    freshness_warning_emitted = False

    if requested_head_sha:
        try:
            current_head_sha = await _get_current_pr_head_sha(owner, repo, pull_number)
        except Exception as exc:
            logger.warning(
                "github_review_task pre-skill freshness guard fetch failed for %s/%s#%s: %s",
                owner,
                repo,
                pull_number,
                exc,
            )
            runtime_events.append(
                _event(
                    "task.github_review.freshness_guard.warning",
                    "warning",
                    {"expected_head_sha": requested_head_sha, "error": str(exc), **automation_trace},
                )
            )
            freshness_warning_emitted = True
        else:
            if current_head_sha and current_head_sha != requested_head_sha:
                runtime_events.append(
                    _event(
                        "task.github_review.superseded",
                        "stale",
                        {
                            "error_code": "superseded_by_new_head_sha",
                            "stale": True,
                            "expected_head_sha": requested_head_sha,
                            "current_head_sha": current_head_sha,
                            "secondary_action_id": "adapter:github:review_pull_request",
                            **automation_trace,
                        },
                    )
                )
                return {
                    "task_type": "github_review_task",
                    "success": False,
                    "stale": True,
                    "error": "superseded_by_new_head_sha",
                    "error_code": "superseded_by_new_head_sha",
                    "expected_head_sha": requested_head_sha,
                    "current_head_sha": current_head_sha,
                    "owner": owner,
                    "repo": repo,
                    "pull_number": pull_number,
                    "review_summary": None,
                    "review_event": requested_event or "COMMENT",
                    "review_written": False,
                    "comment_written": False,
                    "secondary_action_attempted": False,
                    "secondary_action_success": False,
                    "secondary_action_id": "adapter:github:review_pull_request",
                    "actions_applied": [],
                    "result": {},
                    "skill_name": skill_name,
                    "runtime_events": runtime_events,
                    "source": source,
                    "rule_id": rule_id,
                    "automation_rule_id": automation_rule_id,
                    "dedupe_key": dedupe_key,
                    "review_target": review_target,
                }

    skill_result = await _execute_review_skill_via_chat_loop(
        payload=payload,
        owner=owner,
        repo=repo,
        pull_number=pull_number,
        skill_name=skill_name,
        requested_event=requested_event,
        requested_head_sha=requested_head_sha or None,
        review_target=review_target,
        review_metadata=review_metadata,
        skill_kwargs=resolved_skill_kwargs,
        automation_trace=automation_trace,
    )

    if isinstance(skill_result, dict):
        skill_success = bool(skill_result.get("success"))
        skill_error = skill_result.get("error")
        skill_output = str(skill_result.get("output") or "")
        skill_data = skill_result.get("data") if isinstance(skill_result.get("data"), dict) else {}
        runtime_events.extend(skill_result.get("runtime_events") or [])
    else:
        skill_success = bool(getattr(skill_result, "success", False))
        skill_error = getattr(skill_result, "error", None)
        skill_output = str(getattr(skill_result, "output", "") or "")
        skill_data = getattr(skill_result, "data", {}) if isinstance(getattr(skill_result, "data", None), dict) else {}
    normalized_skill_error = skill_error
    if not skill_success:
        if isinstance(skill_error, str):
            normalized_skill_error = skill_error.strip() or None
        elif skill_error:
            normalized_skill_error = str(skill_error).strip() or None
        else:
            normalized_skill_error = None

        if not normalized_skill_error:
            if isinstance(skill_output, str) and skill_output.strip():
                normalized_skill_error = skill_output.strip()
            else:
                normalized_skill_error = f"GitHub review skill '{skill_name}' failed without an explicit error"

    runtime_events.append(_event("task.github_review.skill.completed" if skill_success else "task.github_review.skill.failed", "completed" if skill_success else "failed", {
        "skill_name": skill_name,
        "success": skill_success,
        "error": normalized_skill_error,
        "execution_mode": "chat_tool_loop",
        "chat_session_id": skill_data.get("chat_session_id"),
        "chat_request_id": skill_data.get("chat_request_id"),
        **automation_trace,
    }))

    review_event, review_summary = _normalize_review_writeback(
        skill_output,
        skill_data,
        review_comment_input,
        requested_event=requested_event,
    )
    writeback_mode = str(payload.get("writeback_mode") or "").strip().lower()
    secondary_action_id = "adapter:github:add_comment" if writeback_mode == "issue_comment" else "adapter:github:review_pull_request"
    action_gate = payload.get("_action_gate") if callable(payload.get("_action_gate")) else None

    secondary_action_attempted = False
    secondary_action_success = False
    actions_applied = []
    error_value = normalized_skill_error

    if skill_success and review_summary:
        action_payload = {
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
            "comment": review_summary,
        }
        if secondary_action_id == "adapter:github:review_pull_request":
            action_payload["review_event"] = review_event

        if requested_head_sha:
            current_head_sha: str | None = None
            try:
                current_head_sha = await _get_current_pr_head_sha(owner, repo, int(pull_number))
            except Exception as exc:
                logger.warning(
                    "github_review_task freshness guard fetch failed for %s/%s#%s: %s",
                    owner,
                    repo,
                    pull_number,
                    exc,
                )
                if not freshness_warning_emitted:
                    runtime_events.append(
                        _event(
                            "task.github_review.freshness_guard.warning",
                            "warning",
                            {"expected_head_sha": requested_head_sha, "error": str(exc), **automation_trace},
                        )
                    )
            if current_head_sha and current_head_sha != requested_head_sha:
                runtime_events.append(
                    _event(
                        "task.github_review.superseded",
                        "stale",
                        {
                            "error_code": "superseded_by_new_head_sha",
                            "stale": True,
                            "expected_head_sha": requested_head_sha,
                            "current_head_sha": current_head_sha,
                            "secondary_action_id": secondary_action_id,
                            **automation_trace,
                        },
                    )
                )
                return {
                    "task_type": "github_review_task",
                    "success": False,
                    "stale": True,
                    "error": "superseded_by_new_head_sha",
                    "error_code": "superseded_by_new_head_sha",
                    "expected_head_sha": requested_head_sha,
                    "current_head_sha": current_head_sha,
                    "owner": owner,
                    "repo": repo,
                    "pull_number": pull_number,
                    "review_summary": review_summary or None,
                    "review_event": review_event,
                    "review_written": False,
                    "comment_written": False,
                    "secondary_action_attempted": False,
                    "secondary_action_success": False,
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
                    "source": source,
                    "rule_id": rule_id,
                    "automation_rule_id": automation_rule_id,
                    "dedupe_key": dedupe_key,
                    "review_target": review_target,
                }

        secondary_action_attempted = True
        gate = action_gate(secondary_action_id, action_payload) if action_gate else None
        gate_reason = None
        gate_message = None
        is_blocked = False
        if isinstance(gate, dict):
            gate_reason = gate.get("reason")
            gate_message = gate.get("message")
            if "blocked" in gate:
                is_blocked = bool(gate.get("blocked"))
            else:
                is_blocked = bool(gate)
        elif gate is not None:
            is_blocked = bool(gate)

        if is_blocked:
            error_value = "capability policy blocked for secondary action"
            actions_applied.append(
                {
                    "action_id": secondary_action_id,
                    "success": False,
                    "blocked": True,
                    "reason": gate_reason,
                    "message": gate_message,
                    "error": error_value,
                }
            )
            runtime_events.append(
                _event(
                    "task.github_review.secondary_action.blocked",
                    "blocked",
                    {
                        "secondary_action_id": secondary_action_id,
                        "reason": gate_reason,
                        "message": gate_message,
                        **automation_trace,
                    },
                )
            )
        else:
            writeback_payload = dict(payload)
            writeback_metadata = dict(writeback_payload.get("_execution_metadata") or {})
            if skill_data.get("chat_session_id") and not (
                writeback_payload.get("session_id")
                or writeback_payload.get("_runtime_session_id")
                or writeback_metadata.get("session_id")
                or writeback_metadata.get("chat_session_id")
            ):
                writeback_metadata["chat_session_id"] = skill_data.get("chat_session_id")
            if skill_data.get("chat_agent_id") and not (
                writeback_payload.get("agent_id")
                or writeback_payload.get("_runtime_agent_id")
                or writeback_metadata.get("agent_id")
                or writeback_metadata.get("chat_agent_id")
            ):
                writeback_metadata["chat_agent_id"] = skill_data.get("chat_agent_id")
            writeback_payload["_execution_metadata"] = writeback_metadata
            add_comment_result = await execute_github_review_action(
                secondary_action_id,
                action_payload,
                writeback_payload,
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
                        **automation_trace,
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
                **automation_trace,
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
        "source": source,
        "rule_id": rule_id,
        "automation_rule_id": automation_rule_id,
        "dedupe_key": dedupe_key,
        "review_target": review_target,
    }
