"""Unified execution bus for runtime orchestration."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Protocol
import logging

from src.agents.executor import SkillResult, ToolResult, execute_skill, execute_tool_by_name
from src.agents.subagent import SubAgent
from src.runtime.contracts import ExecutionRequest, ExecutionResult, make_execution_result
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
        self._handlers: Dict[str, ExecutionHandler] = handlers or {}

    def register_handler(self, execution_type: str, handler: ExecutionHandler) -> None:
        self._handlers[execution_type] = handler

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        handler = self._handlers.get(request.execution_type)
        if handler is None:
            result = make_execution_result(
                request_id=request.request_id,
                status="blocked",
                output_payload={"error": f"No handler for execution_type={request.execution_type}"},
            )
            self._emit_runtime_event(request, result)
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

        self._emit_runtime_event(request, result)
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

    def _emit_runtime_event(self, request: ExecutionRequest, result: ExecutionResult) -> None:
        if not self._event_emitter:
            return
        payload = build_runtime_event(
            event_type="execution.completed",
            state=result.status,
            session_id=request.session_id,
            request_id=request.request_id,
            agent_id=request.agent_id,
            summary=f"{request.execution_type} execution {result.status}",
            detail_payload={"output_payload": result.output_payload},
            legacy_payload={
                "type": "execution_completed",
                "execution_type": request.execution_type,
            },
        )
        try:
            self._event_emitter("execution_completed", payload)
        except Exception:
            logger.debug("ExecutionBus event emission failed", exc_info=True)


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
        return await execute_skill(skill_name, **kwargs)

    async def tool_handler(request: ExecutionRequest) -> ToolResult:
        tool_name = _get_required(request.input_payload, "tool_name")
        kwargs = dict(request.input_payload.get("kwargs") or {})
        return await execute_tool_by_name(tool_name, **kwargs)

    async def subagent_handler(request: ExecutionRequest) -> Dict[str, Any]:
        task = _get_required(request.input_payload, "task")
        session_key = request.input_payload.get("session_key") or request.session_id or f"subagent-{request.request_id}"
        subagent = SubAgent(
            session_key=session_key,
            task=task,
            model=request.input_payload.get("model"),
            thinking=request.input_payload.get("thinking"),
            disable_tools=bool(request.input_payload.get("disable_tools", False)),
        )
        await subagent.start()
        await subagent._task
        return {"status": subagent.status, "response": subagent.result, "session_key": session_key}

    async def event_handler(request: ExecutionRequest) -> ExecutionResult:
        target = request.metadata.get("target_execution_type") or request.input_payload.get("target_execution_type")
        if target in bus._handlers and target != "event":
            forwarded = ExecutionRequest(
                request_id=request.request_id,
                source_type=request.source_type,
                source_ref=request.source_ref,
                agent_id=request.agent_id,
                session_id=request.session_id,
                execution_type=target,
                input_payload=request.input_payload,
                context_ref=request.context_ref,
                policy_profile_id=request.policy_profile_id,
                metadata=request.metadata,
            )
            return await bus.execute(forwarded)
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
