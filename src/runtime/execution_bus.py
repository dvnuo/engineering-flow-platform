"""Unified execution bus for runtime orchestration."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol
import logging

from src.agents.executor import SkillResult, ToolResult, execute_tool_by_name, run_skill_execution
from src.agents.subagent import run_subagent_execution
from src.agents.tasks import task_manager
from src.runtime.contracts import ExecutionRequest, ExecutionResult, make_execution_request, make_execution_result
from src.runtime.events import build_runtime_event
from src.runtime.governance import GovernanceHooks, as_governance_bus
from src.runtime.governance_bus import (
    GovernanceAuditRecord,
    GovernanceBus,
    GovernanceDecision,
    build_default_governance_bus,
    governance_audit_runtime_event,
)

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
            self._emit_lifecycle_event("execution.failed", request, "blocked", {"status": result.status, "output_summary": summarize_output_payload(result.output_payload)})
            return await self._safe_after_governance(request, result)
        self._emit_lifecycle_event("execution.started", request, "started", {"status": "started"})
        handler = self._handlers.get(request.execution_type)
        if handler is None:
            result = make_execution_result(
                request_id=request.request_id,
                status="blocked",
                output_payload={"error": f"No handler for execution_type={request.execution_type}"},
            )
            self._emit_lifecycle_event("execution.failed", request, "blocked", {"status": result.status, "output_summary": summarize_output_payload(result.output_payload)})
            return await self._safe_after_governance(request, result)

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
            self._emit_lifecycle_event("execution.failed", request, "error", {"status": result.status, "output_summary": summarize_output_payload(result.output_payload)})
            return await self._safe_after_governance(request, result)

        failure_statuses = {"error", "blocked"}
        lifecycle_event = "execution.failed" if result.status in failure_statuses else "execution.completed"
        self._emit_lifecycle_event(lifecycle_event, request, result.status, {"status": result.status, "output_summary": summarize_output_payload(result.output_payload)})
        return await self._safe_after_governance(request, result)

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
            runtime_events=runtime_events,
            audit_ref=audit_ref,
        )

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
        return await run_skill_execution(skill_name, **kwargs)

    async def tool_handler(request: ExecutionRequest) -> ToolResult:
        # TODO(phase1): unify broader tool call sites through ExecutionBus when policy/hook coverage is verified.
        # For now this adapter intentionally reuses the existing execution stack.
        tool_name = _get_required(request.input_payload, "tool_name")
        kwargs = dict(request.input_payload.get("kwargs") or {})
        return await execute_tool_callable(tool_name, **kwargs)

    async def subagent_handler(request: ExecutionRequest) -> Dict[str, Any]:
        return await run_subagent_execution(
            task=_get_required(request.input_payload, "task"),
            session_key=request.input_payload.get("session_key") or request.session_id or f"subagent-{request.request_id}",
            model=request.input_payload.get("model"),
            thinking=request.input_payload.get("thinking"),
            disable_tools=bool(request.input_payload.get("disable_tools", False)),
            cleanup=request.input_payload.get("cleanup", "delete"),
            start_immediately=bool(request.input_payload.get("start_immediately", False)),
            wait_for_completion=bool(request.input_payload.get("wait_for_completion", False)),
        )

    async def task_handler(request: ExecutionRequest) -> ExecutionResult:
        task_type = request.input_payload.get("task_type")
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
        normalized = _coerce_task_tool_result(raw_task_result)
        return make_execution_result(
            request_id=request.request_id,
            status="success" if normalized["success"] else "error",
            output_payload={
                "task_type": "tool_task",
                "tool_name": tool_name,
                "success": bool(normalized["success"]),
                "content": normalized["content"],
                "error": normalized["error"],
                "task_boundary": True,
                "result": normalized["result"],
            },
            artifacts=normalized["artifacts"],
            runtime_events=normalized["runtime_events"],
            next_action_hint=normalized["next_action_hint"],
            audit_ref=normalized["audit_ref"],
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
    bus.register_handler("subagent", subagent_handler)
    bus.register_handler("event", event_handler)
    return bus
