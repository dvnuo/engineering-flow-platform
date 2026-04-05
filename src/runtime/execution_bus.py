"""Unified execution bus for runtime orchestration."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol
import logging

from src.agents.executor import SkillResult, ToolResult, execute_tool_by_name, run_skill_execution
from src.agents.subagent import run_subagent_execution
from src.runtime.contracts import ExecutionRequest, ExecutionResult, make_execution_request, make_execution_result
from src.runtime.events import build_runtime_event

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
    ):
        self._event_emitter = event_emitter
        self._handlers: Dict[str, ExecutionHandler] = dict(handlers) if handlers is not None else {}

    def register_handler(self, execution_type: str, handler: ExecutionHandler) -> None:
        self._handlers[execution_type] = handler

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self._emit_lifecycle_event("execution.started", request, "started", {"status": "started"})
        handler = self._handlers.get(request.execution_type)
        if handler is None:
            result = make_execution_result(
                request_id=request.request_id,
                status="blocked",
                output_payload={"error": f"No handler for execution_type={request.execution_type}"},
            )
            self._emit_lifecycle_event("execution.failed", request, "blocked", {"status": result.status, "output_summary": summarize_output_payload(result.output_payload)})
            return result

        try:
            raw_result = await handler(request)
            result = self._normalize_result(request, raw_result)
        except Exception as exc:
            logger.exception("ExecutionBus handler failed for %s", request.execution_type)
            result = make_execution_result(
                request_id=request.request_id,
                status="error",
                output_payload={"error": str(exc), "execution_type": request.execution_type},
            )
            self._emit_lifecycle_event("execution.failed", request, "error", {"status": result.status, "output_summary": summarize_output_payload(result.output_payload)})
            return result

        failure_statuses = {"error", "blocked"}
        lifecycle_event = "execution.failed" if result.status in failure_statuses else "execution.completed"
        self._emit_lifecycle_event(lifecycle_event, request, result.status, {"status": result.status, "output_summary": summarize_output_payload(result.output_payload)})
        return result

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


def _get_required(payload: Dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValueError(f"Missing required input_payload field: {key}")
    return payload[key]


def build_default_execution_bus(
    *,
    chat_handler: Optional[ExecutionHandler] = None,
    event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> ExecutionBus:
    bus = ExecutionBus(event_emitter=event_emitter)

    if chat_handler is not None:
        bus.register_handler("chat", chat_handler)

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
        return await execute_tool_by_name(tool_name, **kwargs)

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
    bus.register_handler("subagent", subagent_handler)
    bus.register_handler("event", event_handler)
    return bus
