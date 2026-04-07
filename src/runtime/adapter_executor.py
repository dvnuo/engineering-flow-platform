"""Adapter-backed runtime action execution helpers."""

from __future__ import annotations

from typing import Any, Dict
import json
import os
from aiohttp import ClientSession

from src.runtime.events import build_runtime_event
from src.runtime.capability_adapters import (
    build_github_adapter_capabilities,
    build_jira_adapter_capabilities,
    build_portal_adapter_capabilities,
)


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


def _normalize_adapter_contract(
    *,
    outcome: Dict[str, Any],
    system: str,
    action_name: str,
    runtime_events: list[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "success": bool(outcome.get("success")),
        "error": outcome.get("error"),
        "result": outcome.get("result"),
        "system": outcome.get("system", system),
        "action_name": outcome.get("action_name", action_name),
        "action_id": outcome.get("action_id", action_name),
        "runtime_events": list(runtime_events),
    }


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


async def execute_github_workflow_action(action_name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute GitHub runtime adapter actions via existing src.github APIs."""
    from src import github as github_module

    action = str(action_name or "").strip()
    payload = dict(kwargs or {})

    owner = payload.get("owner")
    repo = payload.get("repo")
    pull_number = payload.get("pull_number")

    if action == "review_pull_request":
        if not owner or not repo or pull_number is None:
            return {
                "success": False,
                "error": "owner, repo, and pull_number are required",
                "system": "github",
                "action_name": action,
            }
        review_comment = payload.get("comment")
        if review_comment and isinstance(review_comment, str) and review_comment.strip():
            summary = review_comment.strip()
            raw = {"summary": summary, "source": "provided_comment"}
        else:
            # Reuse existing github module surface instead of introducing a separate HTTP client.
            pr_text = await github_module.github_get_pr(owner, repo, int(pull_number))
            files_text = await github_module.github_get_pr_files(owner, repo, int(pull_number))
            comments_text = await github_module.github_get_pr_comments(owner, repo, int(pull_number))
            summary = (
                f"Automated review summary for {owner}/{repo}#{pull_number}\n\n"
                f"{pr_text}\n\n{files_text}\n\nExisting review comments snapshot:\n{comments_text}"
            )
            raw = {"summary": summary, "source": "github_api"}
    elif action == "add_comment":
        issue_number = payload.get("issue_number", pull_number)
        comment = payload.get("comment") or payload.get("body")
        if not owner or not repo or issue_number is None or not comment:
            return {
                "success": False,
                "error": "owner, repo, issue_number (or pull_number), and comment are required",
                "system": "github",
                "action_name": action,
            }
        # GitHub PR general comments share the issues comments endpoint.
        raw = await github_module.github_add_comment(owner, repo, int(issue_number), str(comment))
    else:
        return {"success": False, "error": f"Unsupported github action: {action}", "system": "github", "action_name": action}

    success = _result_success(raw)
    return {
        "success": success,
        "error": None if success else str(raw),
        "system": "github",
        "action_name": action,
        "result": raw,
    }


async def execute_adapter_action(action_id: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    normalized_action_id = str(action_id or "").strip().lower()
    payload = dict(kwargs or {})

    system = (
        "jira"
        if normalized_action_id.startswith("adapter:jira:")
        else "github"
        if normalized_action_id.startswith("adapter:github:")
        else "portal"
        if normalized_action_id.startswith("adapter:portal:")
        else "unknown"
    )
    runtime_events = [_event("task.adapter_action.started", "started", {"action_id": normalized_action_id, "system": system})]

    executor = ACTION_ID_TO_EXECUTOR.get(normalized_action_id)
    if executor is None:
        runtime_events.append(
            _event(
                "task.adapter_action.failed",
                "failed",
                {"action_id": normalized_action_id, "system": system, "error": "unsupported_adapter_action"},
            )
        )
        return _normalize_adapter_contract(
            outcome={"success": False, "error": f"Unsupported adapter action: {action_id}", "result": None},
            system=system,
            action_name=normalized_action_id,
            runtime_events=runtime_events,
        )

    outcome = await executor(payload)
    runtime_events.append(
        _event(
            "task.adapter_action.completed" if outcome.get("success") else "task.adapter_action.failed",
            "completed" if outcome.get("success") else "failed",
            {
                "action_id": normalized_action_id,
                "system": outcome.get("system", system),
                "success": bool(outcome.get("success")),
                "error": outcome.get("error"),
            },
        )
    )
    return _normalize_adapter_contract(
        outcome=outcome,
        system=outcome.get("system", system),
        action_name=normalized_action_id,
        runtime_events=runtime_events,
    )


async def _exec_jira(action_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await execute_jira_workflow_action(action_name, payload)


async def _exec_github(action_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await execute_github_workflow_action(action_name, payload)


async def _exec_portal(action_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await execute_portal_control_plane_action(action_name, payload)


ACTION_ID_TO_EXECUTOR = {
    "adapter:jira:read_issue": lambda payload: _exec_jira("read_issue", payload),
    "adapter:jira:update_issue": lambda payload: _exec_jira("update_issue", payload),
    "adapter:jira:assign_issue": lambda payload: _exec_jira("assign_issue", payload),
    "adapter:jira:transition_issue": lambda payload: _exec_jira("transition_issue", payload),
    "adapter:jira:add_comment": lambda payload: _exec_jira("add_comment", payload),
    "adapter:github:review_pull_request": lambda payload: _exec_github("review_pull_request", payload),
    "adapter:github:add_comment": lambda payload: _exec_github("add_comment", payload),
    "adapter:portal:create_delegation": lambda payload: _exec_portal("create_delegation", payload),
    "adapter:portal:list_group_delegations": lambda payload: _exec_portal("list_group_delegations", payload),
    "adapter:portal:get_group_task_board": lambda payload: _exec_portal("get_group_task_board", payload),
    "adapter:portal:list_group_coordination_runs": lambda payload: _exec_portal("list_group_coordination_runs", payload),
    "adapter:portal:get_coordination_run": lambda payload: _exec_portal("get_coordination_run", payload),
    "adapter:portal:get_specialist_pool": lambda payload: _exec_portal("get_specialist_pool", payload),
    "adapter:portal:create_task_agent": lambda payload: _exec_portal("create_task_agent", payload),
    "adapter:portal:delete_task_agent": lambda payload: _exec_portal("delete_task_agent", payload),
}


def validate_enabled_adapter_actions_have_executors() -> list[str]:
    descriptor_ids = {
        descriptor.action_id
        for descriptor in [
            *build_github_adapter_capabilities(),
            *build_jira_adapter_capabilities(),
            *build_portal_adapter_capabilities(),
        ]
        if descriptor.enabled
    }
    registered = set(ACTION_ID_TO_EXECUTOR.keys())
    return sorted(descriptor_ids - registered)


def _normalize_json_field(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _build_portal_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.getenv("PORTAL_INTERNAL_AUTH_TOKEN", "").strip()
    api_key = os.getenv("PORTAL_INTERNAL_API_KEY", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["X-Internal-Api-Key"] = api_key
    return headers


async def _post_portal_json(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    async with ClientSession(headers=headers) as session:
        async with session.post(url, json=payload) as response:
            try:
                data = await response.json()
            except Exception:
                data = {"raw": await response.text()}
            if response.status >= 400:
                return {"success": False, "error": f"Portal request failed: HTTP {response.status}", "result": data}
            return {"success": True, "error": None, "result": data}


async def _get_portal_json(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    async with ClientSession(headers=headers) as session:
        async with session.get(url) as response:
            try:
                data = await response.json()
            except Exception:
                data = {"raw": await response.text()}
            if response.status >= 400:
                return {"success": False, "error": f"Portal request failed: HTTP {response.status}", "result": data}
            return {"success": True, "error": None, "result": data}


async def _delete_portal_json(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    async with ClientSession(headers=headers) as session:
        async with session.delete(url) as response:
            try:
                data = await response.json()
            except Exception:
                data = {"raw": await response.text()}
            if response.status >= 400:
                return {"success": False, "error": f"Portal request failed: HTTP {response.status}", "result": data}
            return {"success": True, "error": None, "result": data}


async def execute_portal_control_plane_action(action_name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    action = str(action_name or "").strip()
    payload = dict(kwargs or {})
    base_url = os.getenv("PORTAL_INTERNAL_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        return {"success": False, "error": "PORTAL_INTERNAL_BASE_URL is not configured", "system": "portal", "action_name": action, "result": None}

    if action not in {
        "create_delegation",
        "list_group_delegations",
        "get_group_task_board",
        "list_group_coordination_runs",
        "get_coordination_run",
        "get_specialist_pool",
        "create_task_agent",
        "delete_task_agent",
    }:
        return {"success": False, "error": f"Unsupported portal action: {action}", "system": "portal", "action_name": action, "result": None}

    if action == "create_delegation":
        required_fields = ["group_id", "leader_agent_id", "assignee_agent_id", "objective", "visibility", "skill_name"]
        missing = [key for key in required_fields if not str(payload.get(key) or "").strip()]
        if missing:
            return {
                "success": False,
                "error": f"Missing required fields: {', '.join(missing)}",
                "system": "portal",
                "action_name": action,
                "result": None,
            }
        payload["scoped_context_payload_json"] = _normalize_json_field(payload.get("scoped_context_payload_json") or payload.get("scoped_context_payload"))
        payload["input_artifacts_json"] = _normalize_json_field(payload.get("input_artifacts_json") or payload.get("input_artifacts"))
        payload["expected_output_schema_json"] = _normalize_json_field(payload.get("expected_output_schema_json") or payload.get("expected_output_schema"))
        payload["retry_policy_json"] = _normalize_json_field(payload.get("retry_policy_json") or payload.get("retry_policy"))
        payload["skill_kwargs_json"] = _normalize_json_field(payload.get("skill_kwargs_json") or payload.get("skill_kwargs"))
        payload.pop("scoped_context_payload", None)
        payload.pop("input_artifacts", None)
        payload.pop("expected_output_schema", None)
        payload.pop("retry_policy", None)
        payload.pop("skill_kwargs", None)
        outcome = await _post_portal_json(f"{base_url}/api/internal/agent-delegations", payload, _build_portal_headers())
    elif action == "list_group_delegations":
        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            return {"success": False, "error": "group_id is required", "system": "portal", "action_name": action, "result": None}
        outcome = await _get_portal_json(
            f"{base_url}/api/internal/agent-groups/{group_id}/delegations",
            _build_portal_headers(),
        )
    elif action == "get_group_task_board":
        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            return {"success": False, "error": "group_id is required", "system": "portal", "action_name": action, "result": None}
        outcome = await _get_portal_json(
            f"{base_url}/api/internal/agent-groups/{group_id}/task-board",
            _build_portal_headers(),
        )
    elif action == "list_group_coordination_runs":
        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            return {"success": False, "error": "group_id is required", "system": "portal", "action_name": action, "result": None}
        outcome = await _get_portal_json(
            f"{base_url}/api/internal/agent-groups/{group_id}/coordination-runs",
            _build_portal_headers(),
        )
    elif action == "get_specialist_pool":
        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            return {"success": False, "error": "group_id is required", "system": "portal", "action_name": action, "result": None}
        outcome = await _get_portal_json(
            f"{base_url}/api/internal/agent-groups/{group_id}/specialist-pool",
            _build_portal_headers(),
        )
    elif action == "create_task_agent":
        required_fields = ("group_id", "leader_agent_id", "template_agent_id", "name")
        normalized_required = {key: str(payload.get(key) or "").strip() for key in required_fields}
        missing = [key for key in required_fields if not normalized_required.get(key)]
        if missing:
            return {
                "success": False,
                "error": f"Missing required fields: {', '.join(missing)}",
                "system": "portal",
                "action_name": action,
                "result": None,
            }
        group_id = normalized_required["group_id"]
        normalized_payload = dict(payload)
        for key, value in normalized_required.items():
            normalized_payload[key] = value
        for key in ("capabilities", "constraints", "metadata", "tags", "runtime_config"):
            if key in normalized_payload:
                normalized_payload[key] = _normalize_json_field(normalized_payload.get(key))
        outcome = await _post_portal_json(
            f"{base_url}/api/internal/agent-groups/{group_id}/task-agents",
            normalized_payload,
            _build_portal_headers(),
        )
    elif action == "delete_task_agent":
        group_id = str(payload.get("group_id") or "").strip()
        agent_id = str(payload.get("agent_id") or "").strip()
        if not group_id or not agent_id:
            return {
                "success": False,
                "error": "group_id and agent_id are required",
                "system": "portal",
                "action_name": action,
                "result": None,
            }
        outcome = await _delete_portal_json(
            f"{base_url}/api/internal/agent-groups/{group_id}/task-agents/{agent_id}",
            _build_portal_headers(),
        )
    else:
        coordination_run_id = str(payload.get("coordination_run_id") or "").strip()
        if not coordination_run_id:
            return {
                "success": False,
                "error": "coordination_run_id is required",
                "system": "portal",
                "action_name": action,
                "result": None,
            }
        outcome = await _get_portal_json(
            f"{base_url}/api/internal/coordination-runs/{coordination_run_id}",
            _build_portal_headers(),
        )

    return {
        "success": bool(outcome.get("success")),
        "system": "portal",
        "action_name": action,
        "result": outcome.get("result"),
        "error": outcome.get("error"),
    }
