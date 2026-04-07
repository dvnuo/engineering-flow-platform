"""Unified execution bus for runtime orchestration."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol
import logging
from datetime import datetime

from src.agents.executor import SkillResult, ToolResult, execute_tool_by_name, run_skill_execution
from src.agents.subagent import run_subagent_execution
from src.agents.tasks import task_manager
from src.runtime.adapter_executor import execute_adapter_action
from src.runtime.capability_registry import get_capability_registry
from src.runtime.contracts import (
    DelegationResult,
    ExecutionRequest,
    ExecutionResult,
    make_delegation_result,
    make_execution_request,
    make_execution_result,
)
from src.runtime.events import build_runtime_event
from src.runtime.governance import GovernanceHooks, as_governance_bus
from src.runtime.governance_bus import (
    GovernanceAuditRecord,
    GovernanceBus,
    GovernanceDecision,
    build_default_governance_bus,
    evaluate_capability_constraint_decision,
    governance_audit_runtime_event,
)
from src.runtime.jira_workflow_review import run_jira_workflow_review
from src.runtime.leader_orchestration import dispatch_task_breakdown_as_delegations, run_delegation_cycle

logger = logging.getLogger(__name__)


class ExecutionHandler(Protocol):
    async def __call__(self, request: ExecutionRequest) -> Any:
        ...


class ExecutionBus:
    def __init__(
        self,
        *,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        handlers: Optional[Dict[str, ExecutionHandler]] = None,
        governance: Optional[GovernanceBus | GovernanceHooks] = None,
    ):
        self._event_emitter = event_emitter
        self._handlers: Dict[str, ExecutionHandler] = dict(handlers) if handlers is not None else {}
        self._governance = as_governance_bus(governance)

    def register_handler(self, execution_type: str, handler: ExecutionHandler) -> None:
        self._handlers[execution_type] = handler

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        await self._persist_last_execution_id(request)
        decision = await self._safe_before_governance(request)
        if decision is not None and not decision.allowed:
            result = self._blocked_result(request, reason=decision.reason, audit=decision.audit_record)
            final_result = await self._safe_after_governance(request, result)
            self._emit_terminal_lifecycle_event(request, final_result)
            return final_result
        self._emit_lifecycle_event("execution.started", request, "started", {"status": "started"})
        handler = self._handlers.get(request.execution_type)
        if handler is None:
            result = make_execution_result(
                request_id=request.request_id,
                status="blocked",
                output_payload={"error": f"No handler for execution_type={request.execution_type}"},
            )
            final_result = await self._safe_after_governance(request, result)
            self._emit_terminal_lifecycle_event(request, final_result)
            return final_result

        try:
            raw_result = await handler(request)
            result = self._normalize_result(request, raw_result)
        except Exception as exc:
            error_audit = await self._safe_on_error_governance(request, exc)
            logger.exception("ExecutionBus handler failed for %s", request.execution_type)
            error_payload = _build_error_payload(exc, request.execution_type)
            result = make_execution_result(
                request_id=request.request_id,
                status="error",
                output_payload=error_payload,
            )
            if error_audit is not None:
                result.audit_ref = error_audit.audit_ref
                result.runtime_events.append(
                    governance_audit_runtime_event(
                        request=request,
                        status=result.status,
                        audit_record=error_audit,
                    )
                )
            final_result = await self._safe_after_governance(request, result)
            self._emit_terminal_lifecycle_event(request, final_result)
            return final_result

        final_result = await self._safe_after_governance(request, result)
        self._emit_terminal_lifecycle_event(request, final_result)
        return final_result

    async def _persist_last_execution_id(self, request: ExecutionRequest) -> None:
        if not self._should_persist_last_execution_id(request):
            return
        try:
            # local import keeps runtime dependency light and avoids import cycles at module load.
            from src.sessions.manager import session_manager

            await session_manager.set_last_execution_id(request.session_id, request.request_id)
        except Exception:
            logger.debug("ExecutionBus failed to persist last_execution_id", exc_info=True)

    def _should_persist_last_execution_id(self, request: ExecutionRequest) -> bool:
        if not request.session_id:
            return False
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        return metadata.get("persist_last_execution_id") is True

    async def _safe_before_governance(self, request: ExecutionRequest) -> GovernanceDecision:
        try:
            return await self._governance.before_execute(request)
        except Exception:
            logger.debug("ExecutionBus governance hook failed: %s", "before_execute", exc_info=True)
            return GovernanceDecision(allowed=True)

    async def _safe_after_governance(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        try:
            maybe_result = await self._governance.after_execute(request, result)
            if isinstance(maybe_result, GovernanceDecision):
                if maybe_result.result is not None:
                    return self._normalize_result(request, maybe_result.result)
                if maybe_result.allowed:
                    return result
                return self._blocked_result(request, reason=maybe_result.reason, audit=maybe_result.audit_record)
            if isinstance(maybe_result, ExecutionResult):
                return self._normalize_result(request, maybe_result)
            return result
        except Exception:
            logger.debug("ExecutionBus governance hook failed: %s", "after_execute", exc_info=True)
            return result

    async def _safe_on_error_governance(self, request: ExecutionRequest, error: Exception) -> Optional[GovernanceAuditRecord]:
        try:
            return await self._governance.on_error(request, error)
        except Exception:
            logger.debug("ExecutionBus governance hook failed: %s", "on_error", exc_info=True)
            return None

    def _blocked_result(
        self,
        request: ExecutionRequest,
        *,
        reason: Optional[str],
        audit: Optional[GovernanceAuditRecord],
    ) -> ExecutionResult:
        payload = {
            "error": "Execution blocked by governance policy",
            "reason": reason or "governance_blocked",
            "execution_type": request.execution_type,
        }
        runtime_events = []
        audit_ref = None
        if audit is not None:
            audit_ref = audit.audit_ref
            runtime_events.append(
                governance_audit_runtime_event(
                    request=request,
                    status="blocked",
                    audit_record=audit,
                    detail_payload={"reason": reason or "governance_blocked"},
                )
            )
        return make_execution_result(
            request_id=request.request_id,
            status="blocked",
            output_payload=payload,
            artifacts={
                "governance": {
                    "blocked": True,
                    "deny_reason": reason or "governance_blocked",
                    "execution_type": request.execution_type,
                    "capability_id": (request.input_payload or {}).get("action_id"),
                }
            },
            runtime_events=runtime_events,
            audit_ref=audit_ref,
        )

    def _emit_terminal_lifecycle_event(self, request: ExecutionRequest, result: ExecutionResult) -> None:
        failure_statuses = {"error", "blocked"}
        lifecycle_event = "execution.failed" if result.status in failure_statuses else "execution.completed"
        self._emit_lifecycle_event(
            lifecycle_event,
            request,
            result.status,
            {"status": result.status, "output_summary": summarize_output_payload(result.output_payload)},
        )

    def _resolve_task_id(self, request: ExecutionRequest) -> Optional[str]:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        metadata_task_id = metadata.get("task_id")
        if isinstance(metadata_task_id, str) and metadata_task_id.strip():
            return metadata_task_id.strip()
        payload_task_id = request.input_payload.get("task_id")
        if isinstance(payload_task_id, str) and payload_task_id.strip():
            return payload_task_id.strip()
        return None

    def _attach_task_id_to_runtime_events(self, runtime_events: list[Dict[str, Any]], task_id: Optional[str]) -> list[Dict[str, Any]]:
        if not task_id:
            return runtime_events
        normalized_events: list[Dict[str, Any]] = []
        for event in runtime_events:
            if isinstance(event, dict):
                if not event.get("task_id"):
                    enriched = dict(event)
                    enriched["task_id"] = task_id
                    normalized_events.append(enriched)
                else:
                    normalized_events.append(event)
            else:
                normalized_events.append({"value": event, "task_id": task_id})
        return normalized_events

    def _normalize_result(self, request: ExecutionRequest, raw_result: Any) -> ExecutionResult:
        if isinstance(raw_result, ExecutionResult):
            return raw_result
        if isinstance(raw_result, SkillResult):
            return make_execution_result(
                request_id=request.request_id,
                status="success" if raw_result.success else "error",
                output_payload={
                    "success": raw_result.success,
                    "output": raw_result.output,
                    "error": raw_result.error,
                    "data": raw_result.data,
                },
                artifacts=raw_result.data or {},
            )
        if isinstance(raw_result, ToolResult):
            return make_execution_result(
                request_id=request.request_id,
                status="success" if raw_result.success else "error",
                output_payload={
                    "success": raw_result.success,
                    "content": raw_result.content,
                    "error": raw_result.error,
                },
            )
        if isinstance(raw_result, dict):
            explicit_status = raw_result.get("status")
            if isinstance(explicit_status, str) and explicit_status.strip():
                status = explicit_status.strip()
            else:
                status = "error" if raw_result.get("error") else "success"
            return make_execution_result(
                request_id=request.request_id,
                status=status,
                output_payload=raw_result,
                artifacts=raw_result.get("artifacts", {}) if isinstance(raw_result.get("artifacts"), dict) else {},
                runtime_events=raw_result.get("runtime_events", []) if isinstance(raw_result.get("runtime_events"), list) else [],
            )
        if isinstance(raw_result, str):
            return make_execution_result(
                request_id=request.request_id,
                status="success",
                output_payload={"response": raw_result},
            )
        return make_execution_result(
            request_id=request.request_id,
            status="success",
            output_payload={"value": raw_result},
        )

    def _emit_lifecycle_event(
        self,
        event_type: str,
        request: ExecutionRequest,
        state: str,
        detail_payload: Dict[str, Any],
    ) -> None:
        if not self._event_emitter:
            return
        task_id = self._resolve_task_id(request) if request.execution_type == "task" else None
        legacy_type_map = {
            "execution.started": "execution_started",
            "execution.completed": "execution_completed",
            "execution.failed": "execution_failed",
        }
        payload = build_runtime_event(
            event_type=event_type,
            execution_type=request.execution_type,
            state=state,
            session_id=request.session_id,
            request_id=request.request_id,
            agent_id=request.agent_id,
            summary=f"{request.execution_type} execution {state}",
            task_id=task_id,
            detail_payload={
                "execution_type": request.execution_type,
                **detail_payload,
            },
            legacy_payload={
                "legacy_type": legacy_type_map.get(event_type, "execution_event"),
                "execution_type": request.execution_type,
            },
        )
        try:
            self._event_emitter(legacy_type_map.get(event_type, "execution_event"), payload)
        except Exception:
            logger.debug("ExecutionBus event emission failed", exc_info=True)


def summarize_output_payload(payload: Dict[str, Any], max_len: int = 240) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    if not isinstance(payload, dict):
        return {"preview": str(payload)[:max_len]}
    for key in ("response", "output", "content", "error", "status"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value)
        summary[key] = text if len(text) <= max_len else f"{text[:max_len]}...[truncated]"
    if not summary:
        summary["keys"] = sorted(payload.keys())[:10]
    return summary


def _build_error_payload(error: Exception, execution_type: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "error": str(error),
        "error_type": error.__class__.__name__,
        "execution_type": execution_type,
    }
    try:
        from src.agents.errors import LLMError  # local import to minimize coupling risk

        if isinstance(error, LLMError):
            payload["error"] = getattr(error, "message", str(error))
            semantic_error_type = getattr(error, "error_type", None)
            if isinstance(semantic_error_type, str) and semantic_error_type.strip():
                payload["error_type"] = semantic_error_type.strip()
            else:
                payload["error_type"] = payload.get("error_type") or "LLMError"
            payload["exception_class"] = "LLMError"
            status_code = getattr(error, "status_code", None)
            if status_code is not None:
                payload["status_code"] = status_code
            details = getattr(error, "details", None)
            if isinstance(details, dict):
                payload["details"] = details
            provider = getattr(error, "provider", None)
            if isinstance(provider, str) and provider.strip():
                payload["provider"] = provider.strip()
    except Exception:
        pass
    return payload


def _get_required(payload: Dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValueError(f"Missing required input_payload field: {key}")
    return payload[key]


def _coerce_task_tool_result(raw_result: Any) -> Dict[str, Any]:
    """Normalize task tool results from heterogeneous return types."""
    if isinstance(raw_result, ToolResult) or (
        hasattr(raw_result, "success") and hasattr(raw_result, "content") and hasattr(raw_result, "error")
    ):
        success_value = bool(getattr(raw_result, "success"))
        content_value = getattr(raw_result, "content")
        error_value = getattr(raw_result, "error")
        return {
            "success": success_value,
            "content": content_value,
            "error": error_value,
            "result": {
                "success": success_value,
                "content": content_value,
                "error": error_value,
            },
            "artifacts": {},
            "runtime_events": [],
            "next_action_hint": None,
            "audit_ref": None,
        }

    if isinstance(raw_result, ExecutionResult):
        payload = raw_result.output_payload if isinstance(raw_result.output_payload, dict) else {"value": raw_result.output_payload}
        content = payload.get("content", payload.get("output", payload.get("response", payload.get("value"))))
        success = raw_result.status not in {"error", "blocked"}
        return {
            "success": success,
            "content": content,
            "error": payload.get("error"),
            "result": payload,
            "artifacts": raw_result.artifacts if isinstance(raw_result.artifacts, dict) else {},
            "runtime_events": raw_result.runtime_events if isinstance(raw_result.runtime_events, list) else [],
            "next_action_hint": raw_result.next_action_hint,
            "audit_ref": raw_result.audit_ref,
        }

    if isinstance(raw_result, dict):
        explicit_success = raw_result.get("success")
        explicit_status = raw_result.get("status")
        if isinstance(explicit_success, bool):
            success = explicit_success
        elif isinstance(explicit_status, str) and explicit_status.strip():
            success = explicit_status.strip() not in {"error", "blocked"}
        else:
            success = False if raw_result.get("error") else True
        content = raw_result.get("content", raw_result.get("output", raw_result.get("response", raw_result.get("value"))))
        error_value = raw_result.get("error")
        if (error_value is None or error_value == "") and not success:
            if isinstance(content, str) and content.strip():
                error_value = content.strip()
            elif isinstance(explicit_status, str) and explicit_status.strip():
                error_value = f"Task tool result reported status={explicit_status.strip()}"
        return {
            "success": success,
            "content": content,
            "error": error_value,
            "result": raw_result,
            "artifacts": {},
            "runtime_events": [],
            "next_action_hint": None,
            "audit_ref": None,
        }

    if isinstance(raw_result, str):
        return {
            "success": True,
            "content": raw_result,
            "error": None,
            "result": {"value": raw_result},
            "artifacts": {},
            "runtime_events": [],
            "next_action_hint": None,
            "audit_ref": None,
        }

    return {
        "success": True,
        "content": raw_result,
        "error": None,
        "result": {"value": raw_result},
        "artifacts": {},
        "runtime_events": [],
        "next_action_hint": None,
        "audit_ref": None,
    }


def _normalize_capability_component(value: Any) -> str:
    return "_".join(str(value or "").strip().lower().split())


def _resolve_tool_capability(tool_name: str) -> Dict[str, Any]:
    normalized_tool_name = _normalize_capability_component(tool_name)
    capability_id = f"tool:{normalized_tool_name}" if normalized_tool_name else None
    fallback = {
        "capability_id": capability_id,
        "capability_type": "tool",
        "capability_name": tool_name,
        "policy_tags": [],
        "requires_identity_binding": False,
        "capability_resolution": "unresolved",
    }
    if not capability_id:
        return fallback
    descriptor = get_capability_registry().get(capability_id)
    if descriptor is None:
        return fallback
    return {
        "capability_id": descriptor.capability_id,
        "capability_type": descriptor.type or "tool",
        "capability_name": tool_name,
        "policy_tags": list(descriptor.policy_tags or []),
        "requires_identity_binding": bool(descriptor.requires_identity_binding),
        "capability_resolution": "resolved",
    }


def _normalize_tool_execution_outcome(
    *,
    tool_name: str,
    raw_result: Any,
    task_boundary: bool,
    task_type: Optional[str] = None,
) -> Dict[str, Any]:
    normalized = _coerce_task_tool_result(raw_result)
    capability = _resolve_tool_capability(tool_name)
    payload: Dict[str, Any] = {
        "tool_name": tool_name,
        "success": bool(normalized["success"]),
        "content": normalized["content"],
        "error": normalized["error"],
        "result": normalized["result"],
        "capability_id": capability.get("capability_id"),
        "capability_type": capability.get("capability_type"),
        "capability_name": capability.get("capability_name"),
        "policy_tags": capability.get("policy_tags"),
        "requires_identity_binding": capability.get("requires_identity_binding"),
        "capability_resolution": capability.get("capability_resolution"),
    }
    if task_boundary:
        payload["task_type"] = task_type or "tool_task"
        payload["task_boundary"] = True

    return {
        "success": bool(normalized["success"]),
        "output_payload": payload,
        "artifacts": normalized["artifacts"],
        "runtime_events": normalized["runtime_events"],
        "next_action_hint": normalized["next_action_hint"],
        "audit_ref": normalized["audit_ref"],
    }


def _as_list_of_dicts(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _as_list_of_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append(text)
            elif item is not None:
                text = str(item).strip()
                if text:
                    normalized.append(text)
        return normalized
    return []


def _resolve_task_capability(task_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_task_type = str(task_type or "").strip().lower()
    registry = get_capability_registry()
    fallback: Dict[str, Any] = {
        "capability_id": None,
        "capability_type": None,
        "policy_tags": [],
        "requires_identity_binding": False,
        "capability_resolution": "unresolved",
    }
    if normalized_task_type == "adapter_action_task":
        action_id = str(payload.get("action_id") or "").strip().lower()
        descriptor = registry.get(action_id) if action_id else None
        if descriptor is None:
            return {**fallback, "capability_id": action_id or None, "action_id": action_id or None}
        return {
            "capability_id": descriptor.capability_id,
            "capability_type": descriptor.type,
            "policy_tags": list(descriptor.policy_tags or []),
            "requires_identity_binding": bool(descriptor.requires_identity_binding),
            "capability_resolution": "resolved",
            "action_id": descriptor.capability_id,
        }
    if normalized_task_type == "jira_workflow_review_task":
        descriptor = registry.get("adapter:jira:read_issue")
        if descriptor is None:
            return fallback
        return {
            "capability_id": descriptor.capability_id,
            "capability_type": descriptor.type,
            "policy_tags": list(descriptor.policy_tags or []),
            "requires_identity_binding": bool(descriptor.requires_identity_binding),
            "capability_resolution": "resolved",
            "action_id": descriptor.capability_id,
        }
    if normalized_task_type == "github_review_task":
        descriptor = registry.get("adapter:github:review_pull_request")
        if descriptor is None:
            return fallback
        return {
            "capability_id": descriptor.capability_id,
            "capability_type": descriptor.type,
            "policy_tags": list(descriptor.policy_tags or []),
            "requires_identity_binding": bool(descriptor.requires_identity_binding),
            "capability_resolution": "resolved",
            "action_id": descriptor.capability_id,
        }
    if normalized_task_type == "delegation_task":
        skill_name = _non_empty_string(payload.get("skill_name"))
        if not skill_name:
            return fallback
        capability_id = f"skill:{skill_name.lower()}"
        descriptor = registry.get(capability_id)
        if descriptor is None:
            return {
                **fallback,
                "capability_id": capability_id,
                "capability_type": "skill",
                "capability_resolution": "unresolved",
            }
        return {
            "capability_id": descriptor.capability_id,
            "capability_type": descriptor.type,
            "policy_tags": list(descriptor.policy_tags or []),
            "requires_identity_binding": bool(descriptor.requires_identity_binding),
            "capability_resolution": "resolved",
        }
    return fallback


def _resolve_involved_capability_ids(task_type: str, payload: Dict[str, Any]) -> list[str]:
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


def _action_name_to_capability_id(system: str, action_name: str) -> str:
    return f"adapter:{system}:{str(action_name or '').strip().lower()}"


def _evaluate_secondary_action_gate(
    *,
    request: ExecutionRequest,
    action_id: str,
) -> Optional[Dict[str, str]]:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    return evaluate_capability_constraint_decision(
        metadata=metadata,
        capability_id=action_id,
        capability_type="adapter_action",
        action_id=action_id,
    )


def _append_secondary_action_event(
    *,
    runtime_events: list[Dict[str, Any]],
    request: ExecutionRequest,
    task_id: Optional[str],
    event_type: str,
    state: str,
    detail_payload: Dict[str, Any],
    ) -> None:
    runtime_events.append(
        build_runtime_event(
            event_type=event_type,
            execution_type=request.execution_type,
            state=state,
            session_id=request.session_id,
            request_id=request.request_id,
            agent_id=request.agent_id,
            summary="secondary action governance",
            task_id=task_id,
            detail_payload=detail_payload,
            legacy_payload={"legacy_type": "task_secondary_action"},
        )
    )


def _append_secondary_governance_audit_event(
    *,
    runtime_events: list[Dict[str, Any]],
    request: ExecutionRequest,
    task_type: str,
    action_id: str,
    deny_reason: Optional[str],
) -> None:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    policy_profile_id = request.policy_profile_id or metadata.get("policy_profile_id") or "default"
    audit_record = GovernanceAuditRecord(
        audit_ref=f"gov-{request.request_id}-secondary-{action_id.split(':')[-1]}",
        stage="secondary_action",
        message="Blocked secondary action by capability policy",
        metadata={
            "stage": "secondary_action",
            "status": "blocked",
            "rule": "capability_policy",
            "capability_id": action_id,
            "capability_type": "adapter_action",
            "action_id": action_id,
            "deny_reason": deny_reason or "capability_policy_blocked",
            "task_type": task_type,
            "policy_profile_id": policy_profile_id,
        },
    )
    runtime_events.append(
        governance_audit_runtime_event(
            request=request,
            status="blocked",
            audit_record=audit_record,
        )
    )


def _record_secondary_action_decision(
    decisions: list[Dict[str, Any]],
    *,
    action_id: str,
    decision: str,
    reason: Optional[str],
    success: bool,
) -> None:
    decisions.append(
        {
            "action_id": action_id,
            "decision": decision,
            "reason": reason,
            "success": bool(success),
        }
    )


def _build_secondary_governance_summary(
    *,
    governed_secondary_action_ids: list[str],
    blocked_secondary_action_ids: list[str],
    applied_secondary_action_ids: list[str],
    secondary_action_decisions: list[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "governed_secondary_action_ids": sorted(set(governed_secondary_action_ids)),
        "blocked_secondary_action_ids": sorted(set(blocked_secondary_action_ids)),
        "applied_secondary_action_ids": sorted(set(applied_secondary_action_ids)),
        "secondary_action_decisions": list(secondary_action_decisions),
    }


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _non_empty_string(value: Any) -> Optional[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _normalize_agent_mode(value: Any) -> Optional[str]:
    normalized = _non_empty_string(value)
    if normalized in {"specialist", "task"}:
        return normalized
    return None


def _build_structured_delegation_payload_from_skill_output(
    *,
    raw_skill_result: Dict[str, Any],
    success: bool,
    error: Optional[str],
    runtime_audit_trace: Dict[str, Any],
) -> Dict[str, Any]:
    nested = raw_skill_result.get("delegation_result")
    source = nested if isinstance(nested, dict) else raw_skill_result

    summary = source.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        for candidate in (
            raw_skill_result.get("summary"),
            raw_skill_result.get("output"),
            raw_skill_result.get("content"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                summary = candidate.strip()
                break
        else:
            summary = None

    artifacts = _as_list_of_dicts(source.get("artifacts"))
    if not artifacts:
        artifacts = _as_list_of_dicts(raw_skill_result.get("artifacts"))

    blockers = _as_list_of_strings(source.get("blockers"))
    if not blockers:
        blockers = _as_list_of_strings(raw_skill_result.get("blockers"))
    if not blockers and not success:
        blockers = [str(error or "skill_execution_failed")]

    next_recommendation = source.get("next_recommendation")
    if not isinstance(next_recommendation, str) or not next_recommendation.strip():
        raw_next = raw_skill_result.get("next_recommendation")
        if isinstance(raw_next, str) and raw_next.strip():
            next_recommendation = raw_next.strip()
        elif not success:
            next_recommendation = "review_blockers"
        else:
            next_recommendation = None

    audit_trace = _as_dict(source.get("audit_trace"))
    if not audit_trace:
        audit_trace = _as_dict(raw_skill_result.get("audit_trace"))
    audit_trace = {**audit_trace, **runtime_audit_trace}

    return {
        "summary": summary,
        "artifacts": artifacts,
        "blockers": blockers,
        "next_recommendation": next_recommendation,
        "audit_trace": audit_trace,
    }


def _extract_strict_delegation_payload_from_skill_output(
    *,
    raw_skill_result: Dict[str, Any],
    runtime_audit_trace: Dict[str, Any],
) -> tuple[Dict[str, Any], list[str]]:
    validation_errors: list[str] = []
    nested = raw_skill_result.get("delegation_result")
    if not isinstance(nested, dict):
        validation_errors.append("delegation_result must be an object")
        return (
            {
                "summary": None,
                "artifacts": [],
                "blockers": [],
                "next_recommendation": None,
                "audit_trace": dict(runtime_audit_trace),
                "status": "failed",
            },
            validation_errors,
        )

    summary = nested.get("summary")
    artifacts = nested.get("artifacts", [])
    blockers = nested.get("blockers", [])
    next_recommendation = nested.get("next_recommendation")
    nested_status = nested.get("status")

    audit_trace = nested.get("audit_trace")
    if isinstance(audit_trace, dict):
        merged_audit_trace = {**audit_trace, **runtime_audit_trace}
    else:
        merged_audit_trace = dict(runtime_audit_trace)
        if "audit_trace" in nested:
            validation_errors.append("audit_trace must be a dict")

    return (
        {
            "summary": summary,
            "artifacts": artifacts,
            "blockers": blockers,
            "next_recommendation": next_recommendation,
            "audit_trace": merged_audit_trace,
            "status": nested_status,
        },
        validation_errors,
    )


def _validate_delegation_result_payload(payload: Dict[str, Any]) -> list[str]:
    errors: list[str] = []
    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        errors.append("summary must be str or None")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("artifacts must be a list")
    blockers = payload.get("blockers")
    if not isinstance(blockers, list):
        errors.append("blockers must be a list")
    next_recommendation = payload.get("next_recommendation")
    if next_recommendation is not None and not isinstance(next_recommendation, str):
        errors.append("next_recommendation must be str or None")
    audit_trace = payload.get("audit_trace")
    if not isinstance(audit_trace, dict):
        errors.append("audit_trace must be a dict")
    status = payload.get("status")
    if status not in {"completed", "failed", "blocked"}:
        errors.append("status must be one of: completed, failed, blocked")
    return errors


def _schema_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return True


def _validate_expected_output_schema(payload: Dict[str, Any], schema: Dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in payload:
                errors.append(f"missing required field: {key}")

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, rules in properties.items():
            if key not in payload or not isinstance(rules, dict):
                continue
            expected_type = rules.get("type")
            if isinstance(expected_type, str) and not _schema_type_matches(payload.get(key), expected_type):
                errors.append(f"field '{key}' expected type '{expected_type}'")
    return errors


def build_default_execution_bus(
    *,
    chat_handler: Optional[ExecutionHandler] = None,
    event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    governance: Optional[GovernanceBus | GovernanceHooks] = None,
    execute_tool_func: Optional[Callable[..., Any]] = None,
) -> ExecutionBus:
    """Build a bus with default skill/tool/subagent/event handlers.

    Chat handling is intentionally caller-injected via `chat_handler` to avoid
    coupling this runtime module to a single chat orchestration implementation.
    """
    bus = ExecutionBus(event_emitter=event_emitter, governance=governance or build_default_governance_bus())

    if chat_handler is not None:
        # Chat is intentionally optional and injected by caller (e.g., webchat/gateway path).
        bus.register_handler("chat", chat_handler)

    execute_tool_callable = execute_tool_func or execute_tool_by_name

    async def skill_handler(request: ExecutionRequest) -> SkillResult:
        skill_name = _get_required(request.input_payload, "skill_name")
        kwargs = dict(request.input_payload.get("kwargs") or {})
        kwargs.setdefault("session_id", request.session_id)
        return await run_skill_execution(skill_name, _via_execution_bus=True, **kwargs)

    async def tool_handler(request: ExecutionRequest) -> ExecutionResult:
        tool_name = _get_required(request.input_payload, "tool_name")
        kwargs = dict(request.input_payload.get("kwargs") or {})
        raw_tool_result = await execute_tool_callable(tool_name, **kwargs)
        normalized = _normalize_tool_execution_outcome(
            tool_name=tool_name,
            raw_result=raw_tool_result,
            task_boundary=False,
        )
        return make_execution_result(
            request_id=request.request_id,
            status="success" if normalized["success"] else "error",
            output_payload=normalized["output_payload"],
            artifacts=normalized["artifacts"],
            runtime_events=normalized["runtime_events"],
            next_action_hint=normalized["next_action_hint"],
            audit_ref=normalized["audit_ref"],
        )

    async def subagent_handler(request: ExecutionRequest) -> Dict[str, Any]:
        return await run_subagent_execution(
            task=_get_required(request.input_payload, "task"),
            session_key=request.input_payload.get("session_key") or request.session_id or f"subagent-{request.request_id}",
            parent_session_id=request.session_id,
            model=request.input_payload.get("model"),
            thinking=request.input_payload.get("thinking"),
            disable_tools=bool(request.input_payload.get("disable_tools", False)),
            cleanup=request.input_payload.get("cleanup", "delete"),
            start_immediately=bool(request.input_payload.get("start_immediately", False)),
            wait_for_completion=bool(request.input_payload.get("wait_for_completion", False)),
        )

    async def _execute_delegation_request(
        request: ExecutionRequest,
        *,
        task_id: Optional[str],
        task_type_override: Optional[str] = None,
    ) -> ExecutionResult:
        adapted_payload = dict(request.input_payload or {})
        adapted_payload["task_type"] = "delegation_task"
        adapted_request = make_execution_request(
            request_id=request.request_id,
            source_type=request.source_type,
            source_ref=request.source_ref,
            agent_id=request.agent_id,
            session_id=request.session_id,
            execution_type=request.execution_type,
            input_payload=adapted_payload,
            context_ref=request.context_ref,
            policy_profile_id=request.policy_profile_id,
            metadata={
                **(request.metadata or {}),
                "_delegation_helper_invoked": True,
                "_delegation_task_id": task_id,
            },
        )
        result = await task_handler(adapted_request)
        if isinstance(result.output_payload, dict) and task_type_override:
            result.output_payload["task_type"] = task_type_override
        return result

    async def task_handler(request: ExecutionRequest) -> ExecutionResult:
        task_id = bus._resolve_task_id(request)
        task_type = request.input_payload.get("task_type")
        if task_type == "delegation_task":
            metadata = request.metadata if isinstance(request.metadata, dict) else {}
            if metadata.get("_delegation_helper_invoked") is not True:
                return await _execute_delegation_request(
                    request,
                    task_id=task_id,
                    task_type_override="delegation_task",
                )
            task_id = metadata.get("_delegation_task_id") or task_id
            delegation_id = _get_required(request.input_payload, "delegation_id")
            objective = _get_required(request.input_payload, "objective")
            visibility = _get_required(request.input_payload, "visibility")
            skill_name = request.input_payload.get("skill_name")
            leader_agent_id = request.input_payload.get("leader_agent_id")
            materialized_context_ref = _as_dict(request.context_ref)
            shared_context_materialized = bool(materialized_context_ref)
            metadata = request.metadata if isinstance(request.metadata, dict) else {}
            strict_delegation_result = bool(
                request.input_payload.get("strict_delegation_result") is True
                or metadata.get("strict_delegation_result") is True
            )
            leader_session_id = _non_empty_string(metadata.get("portal_leader_session_id")) or request.session_id
            ephemeral_task_agent_id = _non_empty_string(request.input_payload.get("ephemeral_task_agent_id"))
            task_agent_template_id = _non_empty_string(request.input_payload.get("task_agent_template_id"))
            task_agent_scope = _non_empty_string(request.input_payload.get("task_agent_scope"))
            task_agent_cleanup_policy = _non_empty_string(request.input_payload.get("task_agent_cleanup_policy"))
            agent_mode = _normalize_agent_mode(request.input_payload.get("agent_mode"))
            if not agent_mode:
                agent_mode = "task" if ephemeral_task_agent_id else "specialist"
            resolved_shared_context_ref = _non_empty_string(request.input_payload.get("shared_context_ref")) or _non_empty_string(
                metadata.get("shared_context_ref")
            )
            common_event_detail = {
                "delegation_id": delegation_id,
                "group_id": request.input_payload.get("group_id"),
                "leader_agent_id": leader_agent_id,
                "parent_agent_id": request.input_payload.get("parent_agent_id"),
                "assignee_agent_id": request.input_payload.get("assignee_agent_id"),
                "visibility": visibility,
                "skill_name": skill_name,
                "shared_context_ref": resolved_shared_context_ref,
                "scoped_context_ref": request.input_payload.get("scoped_context_ref"),
                "shared_context_materialized": shared_context_materialized,
                "leader_session_id": leader_session_id,
                "strict_delegation_result": strict_delegation_result,
                "agent_mode": agent_mode,
                "ephemeral_task_agent_id": ephemeral_task_agent_id,
                "task_agent_template_id": task_agent_template_id,
                "task_agent_scope": task_agent_scope,
                "task_agent_cleanup_policy": task_agent_cleanup_policy,
            }

            def _delegation_failure_result(
                *,
                error_code: str,
                summary: str,
                blockers: Optional[list[str]] = None,
                status: str = "blocked",
            ) -> ExecutionResult:
                delegation_result = make_delegation_result(
                    delegation_id=delegation_id,
                    assignee_agent_id=request.input_payload.get("assignee_agent_id"),
                    status=status,
                    blockers=list(blockers or [error_code]),
                    summary=summary,
                    raw_result={"error": error_code},
                    audit_trace={
                        "request_id": request.request_id,
                        "task_id": task_id,
                        "leader_agent_id": leader_agent_id,
                        "leader_session_id": leader_session_id,
                        "strict_delegation_result": strict_delegation_result,
                    },
                )
                # Runtime contract remains canonical (summary/artifacts). Portal maps these to DB column names.
                delegation_payload = {
                    "delegation_id": delegation_result.delegation_id,
                    "assignee_agent_id": delegation_result.assignee_agent_id,
                    "status": delegation_result.status,
                    "summary": delegation_result.summary,
                    "artifacts": delegation_result.artifacts,
                    "blockers": delegation_result.blockers,
                    "next_recommendation": delegation_result.next_recommendation,
                    "audit_trace": delegation_result.audit_trace,
                    "raw_result": delegation_result.raw_result,
                }
                runtime_events = [
                    build_runtime_event(
                        event_type="task.delegation.failed",
                        execution_type=request.execution_type,
                        state="failed",
                        session_id=request.session_id,
                        request_id=request.request_id,
                        agent_id=request.agent_id,
                        summary=f"delegation task {delegation_id}",
                        task_id=task_id,
                        detail_payload=dict(common_event_detail),
                        legacy_payload={"legacy_type": "task_delegation"},
                    )
                ]
                return make_execution_result(
                    request_id=request.request_id,
                    status="blocked" if status == "blocked" else "error",
                    output_payload={
                        "task_type": task_type,
                        "delegation_id": delegation_id,
                        "success": False,
                        "delegation_result": delegation_payload,
                        "error": error_code,
                        "task_boundary": True,
                    },
                    runtime_events=runtime_events,
                )

            if visibility not in {"leader_only", "group_visible"}:
                return _delegation_failure_result(
                    error_code=f"unsupported_visibility:{visibility}",
                    summary=f"Delegation blocked: unsupported visibility '{visibility}'",
                    blockers=["unsupported_visibility"],
                )
            if not isinstance(skill_name, str) or not skill_name.strip():
                return _delegation_failure_result(
                    error_code="missing_skill_name",
                    summary="Delegation blocked: skill_name is required for delegation_task",
                )
            raw_skill_kwargs = request.input_payload.get("skill_kwargs")
            if raw_skill_kwargs is not None and not isinstance(raw_skill_kwargs, dict):
                return _delegation_failure_result(
                    error_code="invalid_skill_kwargs",
                    summary="Delegation blocked: skill_kwargs must be an object/dict when provided",
                    blockers=["invalid_skill_kwargs"],
                )
            task_agent_context_errors: list[str] = []
            if _non_empty_string(request.input_payload.get("agent_mode")) and agent_mode is None:
                task_agent_context_errors.append("agent_mode must be one of: specialist, task")
            if agent_mode == "task":
                if not strict_delegation_result:
                    task_agent_context_errors.append("strict_delegation_result must be true for task agent mode")
                if not leader_session_id:
                    task_agent_context_errors.append("leader_session_id is required for task agent mode")
                if not ephemeral_task_agent_id:
                    task_agent_context_errors.append("ephemeral_task_agent_id is required for task agent mode")
                if not task_agent_scope:
                    task_agent_context_errors.append("task_agent_scope is required for task agent mode")
            if task_agent_context_errors:
                audit_trace = {
                    "request_id": request.request_id,
                    "task_id": task_id,
                    "leader_agent_id": leader_agent_id,
                    "leader_session_id": leader_session_id,
                    "strict_delegation_result": strict_delegation_result,
                    "agent_mode": agent_mode,
                    "ephemeral_task_agent_id": ephemeral_task_agent_id,
                    "task_agent_template_id": task_agent_template_id,
                    "task_agent_scope": task_agent_scope,
                    "task_agent_cleanup_policy": task_agent_cleanup_policy,
                }
                delegation_result = make_delegation_result(
                    delegation_id=delegation_id,
                    assignee_agent_id=request.input_payload.get("assignee_agent_id"),
                    status="failed",
                    blockers=["invalid_task_agent_context"],
                    summary="Delegation failed: invalid task agent execution context",
                    raw_result={"error": "invalid_task_agent_context"},
                    audit_trace=audit_trace,
                )
                delegation_payload = {
                    "delegation_id": delegation_result.delegation_id,
                    "assignee_agent_id": delegation_result.assignee_agent_id,
                    "status": delegation_result.status,
                    "summary": delegation_result.summary,
                    "artifacts": delegation_result.artifacts,
                    "blockers": delegation_result.blockers,
                    "next_recommendation": delegation_result.next_recommendation,
                    "audit_trace": delegation_result.audit_trace,
                    "raw_result": delegation_result.raw_result,
                }
                runtime_events = [
                    build_runtime_event(
                        event_type="task.delegation.failed",
                        execution_type=request.execution_type,
                        state="failed",
                        session_id=request.session_id,
                        request_id=request.request_id,
                        agent_id=request.agent_id,
                        summary=f"delegation task {delegation_id}",
                        task_id=task_id,
                        detail_payload={**common_event_detail, "task_agent_context_errors": task_agent_context_errors},
                        legacy_payload={"legacy_type": "task_delegation"},
                    )
                ]
                return make_execution_result(
                    request_id=request.request_id,
                    status="error",
                    output_payload={
                        "task_type": task_type,
                        "delegation_id": delegation_id,
                        "success": False,
                        "delegation_result": delegation_payload,
                        "error": "invalid_task_agent_context",
                        "task_boundary": True,
                    },
                    runtime_events=runtime_events,
                )

            pending_record = {
                "delegation_id": delegation_id,
                "task_id": task_id,
                "objective": objective,
                "group_id": request.input_payload.get("group_id"),
                "leader_agent_id": leader_agent_id,
                "parent_agent_id": request.input_payload.get("parent_agent_id"),
                "assignee_agent_id": request.input_payload.get("assignee_agent_id"),
                "visibility": visibility,
                "shared_context_ref": resolved_shared_context_ref,
                "shared_context_materialized": shared_context_materialized,
                "skill_name": skill_name.strip(),
                "leader_session_id": leader_session_id,
                "agent_mode": agent_mode,
                "ephemeral_task_agent_id": ephemeral_task_agent_id,
                "task_agent_template_id": task_agent_template_id,
                "task_agent_scope": task_agent_scope,
                "task_agent_cleanup_policy": task_agent_cleanup_policy,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
            if request.session_id:
                try:
                    from src.sessions.manager import session_manager

                    await session_manager.add_pending_delegation(request.session_id, pending_record)
                except Exception:
                    logger.debug("ExecutionBus failed to add pending delegation metadata", exc_info=True)

            skill_success = False
            skill_error = None
            raw_skill_result: Dict[str, Any] = {}
            try:
                skill_kwargs = dict(raw_skill_kwargs or {})
                # shared_context_ref/scoped_context_ref are logical refs; context_ref is the materialized payload when present.
                delegation_context = {
                    "delegation_id": delegation_id,
                    "group_id": request.input_payload.get("group_id"),
                    "leader_agent_id": leader_agent_id,
                    "parent_agent_id": request.input_payload.get("parent_agent_id"),
                    "assignee_agent_id": request.input_payload.get("assignee_agent_id"),
                    "objective": objective,
                    "shared_context_ref": resolved_shared_context_ref,
                    "scoped_context_ref": request.input_payload.get("scoped_context_ref"),
                    "context_ref": dict(materialized_context_ref),
                    "shared_context_materialized": shared_context_materialized,
                    "input_artifacts": list(request.input_payload.get("input_artifacts") or []),
                    "expected_output_schema": dict(request.input_payload.get("expected_output_schema") or {}),
                    "deadline": request.input_payload.get("deadline"),
                    "retry_policy": dict(request.input_payload.get("retry_policy") or {}),
                    "visibility": visibility,
                    "request_metadata": {
                        key: value
                        for key, value in dict(request.metadata or {}).items()
                        if not str(key).startswith("_delegation_")
                    },
                    "leader_session_id": leader_session_id,
                    "strict_delegation_result": strict_delegation_result,
                    "agent_mode": agent_mode,
                    "ephemeral_task_agent_id": ephemeral_task_agent_id,
                    "task_agent_template_id": task_agent_template_id,
                    "task_agent_scope": task_agent_scope,
                    "task_agent_cleanup_policy": task_agent_cleanup_policy,
                }
                skill_kwargs["delegation_context"] = delegation_context
                skill_kwargs.setdefault("session_id", request.session_id)
                skill_result = await run_skill_execution(skill_name.strip(), _via_execution_bus=True, **skill_kwargs)
                normalized_skill = bus._normalize_result(request, skill_result)
                raw_skill_result = dict(normalized_skill.output_payload or {})
                skill_success = normalized_skill.status not in {"error", "blocked"}
                skill_error = raw_skill_result.get("error")
                runtime_audit_trace = {
                    "request_id": request.request_id,
                    "task_id": task_id,
                    "skill_name": skill_name.strip(),
                    "leader_agent_id": leader_agent_id,
                    "leader_session_id": leader_session_id,
                    "strict_delegation_result": strict_delegation_result,
                    "agent_mode": agent_mode,
                    "ephemeral_task_agent_id": ephemeral_task_agent_id,
                    "task_agent_template_id": task_agent_template_id,
                    "task_agent_scope": task_agent_scope,
                    "task_agent_cleanup_policy": task_agent_cleanup_policy,
                }
                strict_extraction_errors: list[str] = []
                if strict_delegation_result:
                    structured_payload, strict_extraction_errors = _extract_strict_delegation_payload_from_skill_output(
                        raw_skill_result=raw_skill_result,
                        runtime_audit_trace=runtime_audit_trace,
                    )
                else:
                    structured_payload = _build_structured_delegation_payload_from_skill_output(
                        raw_skill_result=raw_skill_result,
                        success=skill_success,
                        error=skill_error,
                        runtime_audit_trace=runtime_audit_trace,
                    )
                delegation_result: DelegationResult = make_delegation_result(
                    delegation_id=delegation_id,
                    assignee_agent_id=request.input_payload.get("assignee_agent_id"),
                    status="completed" if skill_success else "failed",
                    summary=structured_payload["summary"],
                    artifacts=structured_payload["artifacts"],
                    blockers=structured_payload["blockers"],
                    next_recommendation=structured_payload["next_recommendation"],
                    audit_trace=structured_payload["audit_trace"],
                    raw_result=raw_skill_result,
                )
                # Runtime contract remains canonical (summary/artifacts). Portal maps these to DB column names.
                delegation_payload = {
                    "delegation_id": delegation_result.delegation_id,
                    "assignee_agent_id": delegation_result.assignee_agent_id,
                    "status": delegation_result.status,
                    "summary": delegation_result.summary,
                    "artifacts": delegation_result.artifacts,
                    "blockers": delegation_result.blockers,
                    "next_recommendation": delegation_result.next_recommendation,
                    "audit_trace": delegation_result.audit_trace,
                    "raw_result": delegation_result.raw_result,
                }
                if strict_delegation_result and isinstance(structured_payload.get("status"), str):
                    delegation_payload["status"] = structured_payload.get("status")
                delegation_validation_errors = _validate_delegation_result_payload(delegation_payload)
                all_delegation_validation_errors = [*strict_extraction_errors, *delegation_validation_errors]
                if all_delegation_validation_errors:
                    skill_success = False
                    existing_blockers = _as_list_of_strings(delegation_payload.get("blockers"))
                    delegation_payload["blockers"] = [*existing_blockers, "invalid_delegation_result"]
                    runtime_events = list(normalized_skill.runtime_events or [])
                    runtime_events = bus._attach_task_id_to_runtime_events(runtime_events, task_id)
                    runtime_events.append(
                        build_runtime_event(
                            event_type="task.delegation.failed",
                            execution_type=request.execution_type,
                            state="failed",
                            session_id=request.session_id,
                            request_id=request.request_id,
                            agent_id=request.agent_id,
                            summary=f"delegation task {delegation_id}",
                            task_id=task_id,
                            detail_payload={
                                **common_event_detail,
                                "skill_name": skill_name.strip(),
                                "validation_errors": all_delegation_validation_errors,
                            },
                            legacy_payload={"legacy_type": "task_delegation"},
                        )
                    )
                    return make_execution_result(
                        request_id=request.request_id,
                        status="error",
                        output_payload={
                            "task_type": task_type,
                            "delegation_id": delegation_id,
                            "success": False,
                            "delegation_result": delegation_payload,
                            "error": "invalid_delegation_result",
                            "task_boundary": True,
                        },
                        runtime_events=runtime_events,
                    )

                expected_output_schema = request.input_payload.get("expected_output_schema")
                if isinstance(expected_output_schema, dict) and expected_output_schema:
                    schema_errors = _validate_expected_output_schema(delegation_payload, expected_output_schema)
                    if schema_errors:
                        skill_success = False
                        existing_blockers = _as_list_of_strings(delegation_payload.get("blockers"))
                        delegation_payload["blockers"] = [*existing_blockers, "expected_output_schema_validation_failed"]
                        runtime_events = list(normalized_skill.runtime_events or [])
                        runtime_events = bus._attach_task_id_to_runtime_events(runtime_events, task_id)
                        runtime_events.append(
                            build_runtime_event(
                                event_type="task.delegation.failed",
                                execution_type=request.execution_type,
                                state="failed",
                                session_id=request.session_id,
                                request_id=request.request_id,
                                agent_id=request.agent_id,
                                summary=f"delegation task {delegation_id}",
                                task_id=task_id,
                                detail_payload={
                                    **common_event_detail,
                                    "skill_name": skill_name.strip(),
                                    "schema_errors": schema_errors,
                                },
                                legacy_payload={"legacy_type": "task_delegation"},
                            )
                        )
                        return make_execution_result(
                            request_id=request.request_id,
                            status="error",
                            output_payload={
                                "task_type": task_type,
                                "delegation_id": delegation_id,
                                "success": False,
                                "delegation_result": delegation_payload,
                                "error": "expected_output_schema_validation_failed",
                                "task_boundary": True,
                            },
                            runtime_events=runtime_events,
                        )
                runtime_events = list(normalized_skill.runtime_events or [])
                runtime_events = bus._attach_task_id_to_runtime_events(runtime_events, task_id)
                runtime_events.append(
                    build_runtime_event(
                        event_type="task.delegation.completed" if skill_success else "task.delegation.failed",
                        execution_type=request.execution_type,
                        state="completed" if skill_success else "failed",
                        session_id=request.session_id,
                        request_id=request.request_id,
                        agent_id=request.agent_id,
                        summary=f"delegation task {delegation_id}",
                        task_id=task_id,
                        detail_payload={**common_event_detail, "skill_name": skill_name.strip()},
                        legacy_payload={"legacy_type": "task_delegation"},
                    )
                )
                return make_execution_result(
                    request_id=request.request_id,
                    status="success" if skill_success else "error",
                    output_payload={
                        "task_type": task_type,
                        "delegation_id": delegation_id,
                        "success": skill_success,
                        "delegation_result": delegation_payload,
                        "error": skill_error,
                        "task_boundary": True,
                    },
                    runtime_events=runtime_events,
                )
            finally:
                if request.session_id:
                    try:
                        from src.sessions.manager import session_manager

                        await session_manager.complete_pending_delegation(
                            request.session_id,
                            delegation_id,
                            status="completed" if skill_success else "failed",
                        )
                    except Exception:
                        logger.debug("ExecutionBus failed to complete pending delegation metadata", exc_info=True)

        if task_type == "adapter_action_task":
            action_id = _get_required(request.input_payload, "action_id")
            kwargs = dict(request.input_payload.get("kwargs") or {})
            capability = _resolve_task_capability(task_type, {"action_id": action_id})
            descriptor = get_capability_registry().get(action_id)
            if descriptor is None or descriptor.type != "adapter_action":
                return make_execution_result(
                    request_id=request.request_id,
                    status="blocked",
                    output_payload={
                        "task_type": task_type,
                        "action_id": action_id,
                        "success": False,
                        "error": f"Unknown or non-adapter action_id: {action_id}",
                        "task_boundary": True,
                        "capability_id": capability.get("capability_id"),
                        "capability_type": capability.get("capability_type"),
                        "capability_resolution": "unresolved",
                    },
                )
            adapter_result = await execute_adapter_action(action_id, kwargs)
            runtime_events = list(adapter_result.get("runtime_events") or [])
            runtime_events = bus._attach_task_id_to_runtime_events(runtime_events, task_id)
            requires_identity_binding = bool(descriptor.requires_identity_binding)
            runtime_events.append(
                build_runtime_event(
                    event_type="task.adapter_action.completed" if adapter_result.get("success") else "task.adapter_action.failed",
                    execution_type=request.execution_type,
                    state="completed" if adapter_result.get("success") else "failed",
                    session_id=request.session_id,
                    request_id=request.request_id,
                    agent_id=request.agent_id,
                    summary=f"adapter action {action_id}",
                    task_id=task_id,
                    detail_payload={
                        "task_type": task_type,
                        "action_id": action_id,
                        "capability_id": capability.get("capability_id"),
                        "capability_type": capability.get("capability_type"),
                        "policy_tags": capability.get("policy_tags"),
                        "requires_identity_binding": requires_identity_binding,
                        "capability_resolution": capability.get("capability_resolution"),
                        "success": bool(adapter_result.get("success")),
                    },
                    legacy_payload={"legacy_type": "task_adapter_action"},
                )
            )
            return make_execution_result(
                request_id=request.request_id,
                status="success" if adapter_result.get("success") else "error",
                output_payload={
                    "task_type": task_type,
                    "action_id": action_id,
                    "success": bool(adapter_result.get("success")),
                    "error": adapter_result.get("error"),
                    "task_boundary": True,
                    "capability_id": capability.get("capability_id"),
                    "capability_type": capability.get("capability_type"),
                    "policy_tags": capability.get("policy_tags"),
                    "requires_identity_binding": requires_identity_binding,
                    "capability_resolution": capability.get("capability_resolution"),
                    "result": adapter_result.get("result"),
                },
                runtime_events=runtime_events,
            )

        if task_type == "jira_workflow_review_task":
            capability = _resolve_task_capability(task_type, request.input_payload)
            involved_capability_ids = _resolve_involved_capability_ids(task_type, request.input_payload)
            involved_action_ids = list(involved_capability_ids)
            governed_secondary_action_ids = sorted([item for item in involved_action_ids if item != "adapter:jira:read_issue"])
            blocked_secondary_action_ids: list[str] = []
            applied_secondary_action_ids: list[str] = []
            blocked_secondary_action_reasons: Dict[str, str] = {}
            secondary_action_decisions: list[Dict[str, Any]] = []
            workflow_payload = {
                "issue_key": _get_required(request.input_payload, "issue_key"),
                "skill_name": request.input_payload.get("skill_name"),
                "skill_kwargs": request.input_payload.get("skill_kwargs"),
                "success_transition": request.input_payload.get("success_transition"),
                "failure_transition": request.input_payload.get("failure_transition"),
                "success_reassign_to": request.input_payload.get("success_reassign_to"),
                "failure_reassign_to": request.input_payload.get("failure_reassign_to"),
                "explicit_success_assignee": request.input_payload.get("explicit_success_assignee"),
                "explicit_failure_assignee": request.input_payload.get("explicit_failure_assignee"),
                "review_comment_template": request.input_payload.get("review_comment_template"),
                "transition_comment_template": request.input_payload.get("transition_comment_template"),
                "fields_on_success": request.input_payload.get("fields_on_success"),
                "fields_on_failure": request.input_payload.get("fields_on_failure"),
                "workflow_context": request.input_payload.get("workflow_context"),
                "review_comment": request.input_payload.get("review_comment"),
                "transition": request.input_payload.get("transition"),
                "assignee": request.input_payload.get("assignee"),
                "fields": request.input_payload.get("fields"),
                "transition_comment": request.input_payload.get("transition_comment"),
            }
            def _jira_action_gate(action_name: str, _kwargs: Dict[str, Any]) -> Dict[str, Any]:
                action_id = _action_name_to_capability_id("jira", action_name)
                decision = _evaluate_secondary_action_gate(request=request, action_id=action_id)
                if decision is not None:
                    blocked_secondary_action_ids.append(action_id)
                    blocked_secondary_action_reasons[action_id] = str(decision.get("reason") or "capability_policy_blocked")
                    return {
                        "blocked": True,
                        "reason": decision.get("reason"),
                        "error": f"capability policy blocked for secondary action: {action_id}",
                    }
                applied_secondary_action_ids.append(action_id)
                return {"blocked": False}

            workflow_payload["_action_gate"] = _jira_action_gate
            workflow_result = await run_jira_workflow_review(workflow_payload)
            runtime_events = list(workflow_result.get("runtime_events") or [])
            runtime_events = bus._attach_task_id_to_runtime_events(runtime_events, task_id)
            for blocked_action_id in blocked_secondary_action_ids:
                _append_secondary_action_event(
                    runtime_events=runtime_events,
                    request=request,
                    task_id=task_id,
                    event_type="task.jira_workflow_review.secondary_action.blocked",
                    state="blocked",
                    detail_payload={
                        "task_type": task_type,
                        "secondary_action_id": blocked_action_id,
                    },
                )
                _append_secondary_governance_audit_event(
                    runtime_events=runtime_events,
                    request=request,
                    task_type=task_type,
                    action_id=blocked_action_id,
                    deny_reason=blocked_secondary_action_reasons.get(blocked_action_id),
                )
            actions_applied = workflow_result.get("actions_applied") if isinstance(workflow_result.get("actions_applied"), list) else []
            action_results_by_id: Dict[str, Dict[str, Any]] = {}
            for action_item in actions_applied:
                if not isinstance(action_item, dict):
                    continue
                action_name = str(action_item.get("action") or "").strip().lower()
                if not action_name:
                    continue
                action_results_by_id[_action_name_to_capability_id("jira", action_name)] = action_item

            for governed_action_id in governed_secondary_action_ids:
                action_result = action_results_by_id.get(governed_action_id) or {}
                if governed_action_id in blocked_secondary_action_ids or bool(action_result.get("blocked")):
                    _record_secondary_action_decision(
                        secondary_action_decisions,
                        action_id=governed_action_id,
                        decision="blocked",
                        reason=str(action_result.get("error") or blocked_secondary_action_reasons.get(governed_action_id) or "capability_policy_blocked"),
                        success=False,
                    )
                elif action_result.get("success") is True:
                    _record_secondary_action_decision(
                        secondary_action_decisions,
                        action_id=governed_action_id,
                        decision="applied",
                        reason=None,
                        success=True,
                    )
                elif action_result:
                    _record_secondary_action_decision(
                        secondary_action_decisions,
                        action_id=governed_action_id,
                        decision="failed",
                        reason=str(action_result.get("error") or "action_failed"),
                        success=False,
                    )
                else:
                    _record_secondary_action_decision(
                        secondary_action_decisions,
                        action_id=governed_action_id,
                        decision="allowed",
                        reason="not_invoked",
                        success=False,
                    )
            secondary_summary = _build_secondary_governance_summary(
                governed_secondary_action_ids=governed_secondary_action_ids,
                blocked_secondary_action_ids=blocked_secondary_action_ids,
                applied_secondary_action_ids=applied_secondary_action_ids,
                secondary_action_decisions=secondary_action_decisions,
            )
            runtime_events.append(
                build_runtime_event(
                    event_type="task.jira_workflow_review.completed" if workflow_result.get("success") else "task.jira_workflow_review.failed",
                    execution_type=request.execution_type,
                    state="completed" if workflow_result.get("success") else "failed",
                    session_id=request.session_id,
                    request_id=request.request_id,
                    agent_id=request.agent_id,
                    summary="jira workflow review task",
                    task_id=task_id,
                    detail_payload={
                        "task_type": task_type,
                        "capability_id": capability.get("capability_id"),
                        "capability_type": capability.get("capability_type"),
                        "policy_tags": capability.get("policy_tags"),
                        "requires_identity_binding": capability.get("requires_identity_binding"),
                        "capability_resolution": capability.get("capability_resolution"),
                        "involved_capability_ids": involved_capability_ids,
                        "involved_action_ids": involved_action_ids,
                        **secondary_summary,
                        "issue_key": workflow_result.get("issue_key"),
                        "skill_name": workflow_result.get("skill_name") or workflow_payload.get("skill_name"),
                        "workflow_outcome": workflow_result.get("workflow_outcome"),
                        "approved": workflow_result.get("approved"),
                        "success_transition": workflow_payload.get("success_transition"),
                        "failure_transition": workflow_payload.get("failure_transition"),
                        "reassignment_target": workflow_result.get("reassignment_target"),
                        "success": bool(workflow_result.get("success")),
                        "actions_applied": len(workflow_result.get("actions_applied") or []),
                    },
                    legacy_payload={"legacy_type": "task_jira_workflow_review"},
                )
            )
            return make_execution_result(
                request_id=request.request_id,
                status="success" if workflow_result.get("success") else "error",
                output_payload={
                    "task_type": task_type,
                    "success": bool(workflow_result.get("success")),
                    "error": workflow_result.get("error"),
                    "task_boundary": True,
                    "capability_id": capability.get("capability_id"),
                    "capability_type": capability.get("capability_type"),
                    "policy_tags": capability.get("policy_tags"),
                    "requires_identity_binding": capability.get("requires_identity_binding"),
                    "capability_resolution": capability.get("capability_resolution"),
                    "involved_capability_ids": involved_capability_ids,
                    "involved_action_ids": involved_action_ids,
                    **secondary_summary,
                    "resolved_skill_capability_id": f"skill:{str(workflow_payload.get('skill_name') or '').strip().lower()}" if workflow_payload.get("skill_name") else None,
                    "workflow_outcome": workflow_result.get("workflow_outcome"),
                    "actions_applied": workflow_result.get("actions_applied") or [],
                    "result": workflow_result,
                },
                runtime_events=runtime_events,
            )

        if task_type == "github_review_task":
            capability = _resolve_task_capability(task_type, request.input_payload)
            involved_capability_ids = _resolve_involved_capability_ids(task_type, request.input_payload)
            involved_action_ids = list(involved_capability_ids)
            owner = _get_required(request.input_payload, "owner")
            repo = _get_required(request.input_payload, "repo")
            pull_number = _get_required(request.input_payload, "pull_number")
            review_comment_input = request.input_payload.get("comment")
            review_metadata = request.input_payload.get("metadata")

            review_result = await execute_adapter_action(
                "adapter:github:review_pull_request",
                {
                    "owner": owner,
                    "repo": repo,
                    "pull_number": pull_number,
                    "comment": review_comment_input,
                    "metadata": review_metadata,
                },
            )

            runtime_events = list(review_result.get("runtime_events") or [])
            runtime_events = bus._attach_task_id_to_runtime_events(runtime_events, task_id)
            review_summary = None
            if isinstance(review_result.get("result"), dict):
                review_summary = review_result.get("result", {}).get("summary")
            if review_summary is None:
                review_summary = review_comment_input
            comment_written = False
            error_value = review_result.get("error")
            secondary_action_id = "adapter:github:add_comment"
            secondary_action_attempted = False
            secondary_action_success = False
            governed_secondary_action_ids = [secondary_action_id]
            blocked_secondary_action_ids: list[str] = []
            applied_secondary_action_ids: list[str] = []
            secondary_action_decisions: list[Dict[str, Any]] = []

            if review_result.get("success"):
                comment_body = review_summary if isinstance(review_summary, str) and review_summary.strip() else review_comment_input
                if isinstance(comment_body, str) and comment_body.strip():
                    secondary_action_attempted = True
                    secondary_gate = _evaluate_secondary_action_gate(request=request, action_id=secondary_action_id)
                    if secondary_gate is not None:
                        blocked_secondary_action_ids.append(secondary_action_id)
                        error_value = "capability policy blocked for secondary action"
                        _append_secondary_action_event(
                            runtime_events=runtime_events,
                            request=request,
                            task_id=task_id,
                            event_type="task.github_review.secondary_action.blocked",
                            state="blocked",
                            detail_payload={
                                "task_type": task_type,
                                "secondary_action_id": secondary_action_id,
                                "reason": secondary_gate.get("reason"),
                                "message": secondary_gate.get("message"),
                            },
                        )
                        _append_secondary_governance_audit_event(
                            runtime_events=runtime_events,
                            request=request,
                            task_type=task_type,
                            action_id=secondary_action_id,
                            deny_reason=str(secondary_gate.get("reason") or "capability_policy_blocked"),
                        )
                        _record_secondary_action_decision(
                            secondary_action_decisions,
                            action_id=secondary_action_id,
                            decision="blocked",
                            reason=str(secondary_gate.get("reason") or "capability_policy_blocked"),
                            success=False,
                        )
                    else:
                        applied_secondary_action_ids.append(secondary_action_id)
                        add_comment_result = await execute_adapter_action(
                            secondary_action_id,
                            {
                                "owner": owner,
                                "repo": repo,
                                "pull_number": pull_number,
                                "comment": comment_body,
                            },
                        )
                        runtime_events.extend(add_comment_result.get("runtime_events") or [])
                        runtime_events = bus._attach_task_id_to_runtime_events(runtime_events, task_id)
                        comment_written = bool(add_comment_result.get("success"))
                        secondary_action_success = comment_written
                        _append_secondary_action_event(
                            runtime_events=runtime_events,
                            request=request,
                            task_id=task_id,
                            event_type="task.github_review.secondary_action.completed" if comment_written else "task.github_review.secondary_action.failed",
                            state="completed" if comment_written else "failed",
                            detail_payload={
                                "task_type": task_type,
                                "secondary_action_id": secondary_action_id,
                                "success": comment_written,
                                "error": add_comment_result.get("error"),
                            },
                        )
                        if not comment_written:
                            error_value = add_comment_result.get("error") or "Failed to write GitHub review comment"
                            _record_secondary_action_decision(
                                secondary_action_decisions,
                                action_id=secondary_action_id,
                                decision="failed",
                                reason=str(add_comment_result.get("error") or "action_failed"),
                                success=False,
                            )
                        else:
                            _record_secondary_action_decision(
                                secondary_action_decisions,
                                action_id=secondary_action_id,
                                decision="applied",
                                reason=None,
                                success=True,
                            )
                else:
                    error_value = "Review succeeded but no summary/comment text available for write-back"
                    _record_secondary_action_decision(
                        secondary_action_decisions,
                        action_id=secondary_action_id,
                        decision="failed",
                        reason="missing_comment_body",
                        success=False,
                    )
            elif not secondary_action_decisions:
                _record_secondary_action_decision(
                    secondary_action_decisions,
                    action_id=secondary_action_id,
                    decision="allowed",
                    reason="primary_action_failed",
                    success=False,
                )
            secondary_summary = _build_secondary_governance_summary(
                governed_secondary_action_ids=governed_secondary_action_ids,
                blocked_secondary_action_ids=blocked_secondary_action_ids,
                applied_secondary_action_ids=applied_secondary_action_ids,
                secondary_action_decisions=secondary_action_decisions,
            )

            success_value = bool(review_result.get("success")) and comment_written and not error_value
            runtime_events.append(
                build_runtime_event(
                    event_type="task.github_review.completed" if success_value else "task.github_review.failed",
                    execution_type=request.execution_type,
                    state="completed" if success_value else "failed",
                    session_id=request.session_id,
                    request_id=request.request_id,
                    agent_id=request.agent_id,
                    summary="github review task",
                    task_id=task_id,
                    detail_payload={
                        "task_type": task_type,
                        "capability_id": capability.get("capability_id"),
                        "capability_type": capability.get("capability_type"),
                        "policy_tags": capability.get("policy_tags"),
                        "requires_identity_binding": capability.get("requires_identity_binding"),
                        "capability_resolution": capability.get("capability_resolution"),
                        "involved_capability_ids": involved_capability_ids,
                        "involved_action_ids": involved_action_ids,
                        **secondary_summary,
                        "owner": owner,
                        "repo": repo,
                        "pull_number": pull_number,
                        "comment_written": comment_written,
                        "secondary_action_attempted": secondary_action_attempted,
                        "secondary_action_id": secondary_action_id,
                        "secondary_action_success": secondary_action_success,
                        "success": success_value,
                        "error": error_value,
                    },
                    legacy_payload={"legacy_type": "task_github_review"},
                )
            )
            return make_execution_result(
                request_id=request.request_id,
                status="success" if success_value else "error",
                output_payload={
                    "task_type": task_type,
                    "owner": owner,
                    "repo": repo,
                    "pull_number": pull_number,
                    "review_summary": review_summary,
                    "comment_written": comment_written,
                    "success": success_value,
                    "error": error_value,
                    "task_boundary": True,
                    "capability_id": capability.get("capability_id"),
                    "capability_type": capability.get("capability_type"),
                    "policy_tags": capability.get("policy_tags"),
                    "requires_identity_binding": capability.get("requires_identity_binding"),
                    "capability_resolution": capability.get("capability_resolution"),
                    "involved_capability_ids": involved_capability_ids,
                    "involved_action_ids": involved_action_ids,
                    **secondary_summary,
                    "secondary_action_attempted": secondary_action_attempted,
                    "secondary_action_id": secondary_action_id,
                    "secondary_action_success": secondary_action_success,
                },
                runtime_events=runtime_events,
            )

        if task_type != "tool_task":
            return make_execution_result(
                request_id=request.request_id,
                status="blocked",
                output_payload={
                    "task_type": task_type,
                    "success": False,
                    "error": f"Unsupported task_type: {task_type}",
                    "task_boundary": True,
                },
            )
        tool_name = _get_required(request.input_payload, "tool_name")
        kwargs = dict(request.input_payload.get("kwargs") or {})
        event_callback = request.input_payload.get("event_callback")
        raw_task_result = await task_manager.run_tool_task(
            session_id=request.session_id or request.request_id,
            tool_name=tool_name,
            coro_factory=lambda: execute_tool_callable(tool_name, **kwargs),
            event_callback=event_callback if callable(event_callback) else None,
        )
        normalized = _normalize_tool_execution_outcome(
            tool_name=tool_name,
            raw_result=raw_task_result,
            task_boundary=True,
            task_type="tool_task",
        )
        runtime_events = list(normalized["runtime_events"]) if isinstance(normalized["runtime_events"], list) else []
        runtime_events = bus._attach_task_id_to_runtime_events(runtime_events, task_id)
        runtime_events.append(
            build_runtime_event(
                event_type="task.tool.completed" if normalized["success"] else "task.tool.failed",
                execution_type=request.execution_type,
                state="completed" if normalized["success"] else "failed",
                session_id=request.session_id,
                request_id=request.request_id,
                agent_id=request.agent_id,
                summary=f"tool task {tool_name}",
                task_id=task_id,
                detail_payload={
                    "task_type": "tool_task",
                    "tool_name": tool_name,
                    "success": bool(normalized["success"]),
                    "error": normalized["output_payload"].get("error"),
                    "capability_id": normalized["output_payload"].get("capability_id"),
                    "capability_type": normalized["output_payload"].get("capability_type"),
                },
                legacy_payload={"legacy_type": "task_tool"},
            )
        )
        return make_execution_result(
            request_id=request.request_id,
            status="success" if normalized["success"] else "error",
            output_payload=normalized["output_payload"],
            artifacts=normalized["artifacts"],
            runtime_events=runtime_events,
            next_action_hint=normalized["next_action_hint"],
            audit_ref=normalized["audit_ref"],
        )

    async def delegation_handler(request: ExecutionRequest) -> ExecutionResult:
        task_id = bus._resolve_task_id(request)
        return await _execute_delegation_request(
            request,
            task_id=task_id,
            task_type_override="delegation",
        )

    async def coordination_handler(request: ExecutionRequest) -> ExecutionResult:
        coordination_type = _non_empty_string(request.input_payload.get("coordination_type"))
        if coordination_type not in {"delegation_batch", "delegation_cycle"}:
            return make_execution_result(
                request_id=request.request_id,
                status="blocked",
                output_payload={"error": f"Unsupported coordination_type: {coordination_type}", "coordination_type": coordination_type},
            )
        if coordination_type == "delegation_cycle":
            try:
                group_id = _get_required(request.input_payload, "group_id")
                leader_agent_id = _get_required(request.input_payload, "leader_agent_id")
                leader_session_id = _get_required(request.input_payload, "leader_session_id")
                cycle_result = await run_delegation_cycle(
                    group_id=group_id,
                    leader_agent_id=leader_agent_id,
                    leader_session_id=leader_session_id,
                    coordination_run_id=request.input_payload.get("coordination_run_id"),
                    round_index=request.input_payload.get("round_index") if isinstance(request.input_payload.get("round_index"), int) else 1,
                    tasks=request.input_payload.get("tasks") if isinstance(request.input_payload.get("tasks"), list) else [],
                    prior_results=request.input_payload.get("prior_results") if isinstance(request.input_payload.get("prior_results"), list) else [],
                    completion_criteria=request.input_payload.get("completion_criteria")
                    if isinstance(request.input_payload.get("completion_criteria"), dict)
                    else None,
                    request_id=request.request_id,
                )
            except Exception as exc:
                return make_execution_result(
                    request_id=request.request_id,
                    status="error",
                    output_payload={"error": str(exc), "coordination_type": coordination_type},
                )
            delegation_ids = [
                item.get("result", {}).get("delegation_id")
                for item in cycle_result.get("items", [])
                if isinstance(item, dict) and isinstance(item.get("result"), dict) and item.get("result", {}).get("delegation_id")
            ]
            runtime_events = [
                build_runtime_event(
                    event_type="coordination.delegation_cycle.completed"
                    if cycle_result.get("success")
                    else "coordination.delegation_cycle.failed",
                    execution_type=request.execution_type,
                    state="completed" if cycle_result.get("success") else "failed",
                    session_id=request.session_id,
                    request_id=request.request_id,
                    agent_id=request.agent_id,
                    summary="delegation cycle coordination",
                    detail_payload={
                        "coordination_run_id": cycle_result.get("coordination_run_id"),
                        "round_index": cycle_result.get("round_index"),
                        "group_id": group_id,
                        "leader_agent_id": leader_agent_id,
                        "leader_session_id": leader_session_id,
                        "created_count": cycle_result.get("created", 0),
                        "failed_count": cycle_result.get("failed", 0),
                        "delegation_ids": delegation_ids,
                        "is_complete": bool(cycle_result.get("is_complete")),
                        "next_action": cycle_result.get("next_action"),
                        "run_status": cycle_result.get("run_state", {}).get("status"),
                        "completed_at": cycle_result.get("run_state", {}).get("completed_at"),
                        "summary": cycle_result.get("run_state", {}).get("summary", {}),
                        "latest_round_index": cycle_result.get("run_state", {}).get("latest_round_index", cycle_result.get("round_index")),
                        "status_counts": cycle_result.get("run_state", {}).get("status_counts", {}),
                        "all_terminal": cycle_result.get("run_state", {}).get("all_terminal"),
                        "leader_summary_status": cycle_result.get("leader_summary", {}).get("status"),
                    },
                    legacy_payload={"legacy_type": "coordination_delegation_cycle"},
                )
            ]
            return make_execution_result(
                request_id=request.request_id,
                status="success" if cycle_result.get("success") else "error",
                output_payload={
                    "coordination_type": coordination_type,
                    "success": bool(cycle_result.get("success")),
                    "coordination_run_id": cycle_result.get("coordination_run_id"),
                    "round_index": cycle_result.get("round_index"),
                    "created": cycle_result.get("created", 0),
                    "failed": cycle_result.get("failed", 0),
                    "items": cycle_result.get("items", []),
                    "auto_selected_assignee_ids": cycle_result.get("auto_selected_assignee_ids", []),
                    "created_task_agent_ids": cycle_result.get("created_task_agent_ids", []),
                    "deleted_task_agent_ids": cycle_result.get("deleted_task_agent_ids", []),
                    "selection_strategy_used": bool(cycle_result.get("selection_strategy_used", False)),
                    "aggregate": cycle_result.get("aggregate", {}),
                    "run_state": cycle_result.get("run_state", {}),
                    "is_complete": bool(cycle_result.get("is_complete")),
                    "next_action": cycle_result.get("next_action"),
                    "leader_summary": cycle_result.get("leader_summary", {}),
                },
                runtime_events=runtime_events,
            )
        try:
            group_id = _get_required(request.input_payload, "group_id")
            leader_agent_id = _get_required(request.input_payload, "leader_agent_id")
            leader_session_id = _get_required(request.input_payload, "leader_session_id")
            tasks = request.input_payload.get("tasks")
            batch_result = await dispatch_task_breakdown_as_delegations(
                group_id=group_id,
                leader_agent_id=leader_agent_id,
                leader_session_id=leader_session_id,
                tasks=tasks if isinstance(tasks, list) else [],
            )
        except Exception as exc:
            return make_execution_result(
                request_id=request.request_id,
                status="error",
                output_payload={"error": str(exc), "coordination_type": coordination_type},
            )

        delegation_ids = [
            item.get("result", {}).get("delegation_id")
            for item in batch_result.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("result"), dict) and item.get("result", {}).get("delegation_id")
        ]
        runtime_events = [
            build_runtime_event(
                event_type="coordination.delegation_batch.completed" if batch_result.get("success") else "coordination.delegation_batch.failed",
                execution_type=request.execution_type,
                state="completed" if batch_result.get("success") else "failed",
                session_id=request.session_id,
                request_id=request.request_id,
                agent_id=request.agent_id,
                summary="delegation batch coordination",
                detail_payload={
                    "group_id": group_id,
                    "leader_agent_id": leader_agent_id,
                    "leader_session_id": leader_session_id,
                    "created_count": batch_result.get("created", 0),
                    "failed_count": batch_result.get("failed", 0),
                    "delegation_ids": delegation_ids,
                },
                legacy_payload={"legacy_type": "coordination_delegation_batch"},
            )
        ]
        return make_execution_result(
            request_id=request.request_id,
            status="success" if batch_result.get("success") else "error",
            output_payload={
                "coordination_type": coordination_type,
                "success": bool(batch_result.get("success")),
                "created": batch_result.get("created", 0),
                "failed": batch_result.get("failed", 0),
                "items": batch_result.get("items", []),
            },
            runtime_events=runtime_events,
        )

    async def event_handler(request: ExecutionRequest) -> ExecutionResult:
        raw_target = request.metadata.get("target_execution_type") or request.input_payload.get("target_execution_type")
        target = raw_target.strip() if isinstance(raw_target, str) and raw_target.strip() else None
        if target is not None and target != "event" and target in bus._handlers:
            forwarded = make_execution_request(
                source_type=request.source_type,
                source_ref=request.source_ref,
                agent_id=request.agent_id,
                session_id=request.session_id,
                execution_type=target,
                input_payload=request.input_payload,
                context_ref=request.context_ref,
                policy_profile_id=request.policy_profile_id,
                metadata={
                    **(request.metadata or {}),
                    "parent_request_id": request.request_id,
                    "forwarded_from_execution_type": request.execution_type,
                },
            )
            forwarded_result = await bus.execute(forwarded)
            merged_payload: Dict[str, Any] = {}
            if isinstance(forwarded_result.output_payload, dict):
                merged_payload.update(forwarded_result.output_payload)
            merged_payload.update(
                {
                    "forwarded_request_id": forwarded.request_id,
                    "forwarded_execution_type": target,
                    "parent_request_id": request.request_id,
                }
            )
            return make_execution_result(
                request_id=request.request_id,
                status=forwarded_result.status,
                output_payload=merged_payload,
                artifacts=forwarded_result.artifacts,
                runtime_events=forwarded_result.runtime_events,
                next_action_hint=forwarded_result.next_action_hint,
                audit_ref=forwarded_result.audit_ref,
            )
        return make_execution_result(
            request_id=request.request_id,
            status="queued",
            output_payload={"message": "event accepted", "target_execution_type": target},
        )

    bus.register_handler("skill", skill_handler)
    bus.register_handler("tool", tool_handler)
    bus.register_handler("task", task_handler)
    bus.register_handler("delegation", delegation_handler)
    bus.register_handler("coordination", coordination_handler)
    bus.register_handler("subagent", subagent_handler)
    bus.register_handler("event", event_handler)
    return bus

