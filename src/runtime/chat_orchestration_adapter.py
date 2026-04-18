"""Thin runtime bus adapters for chat/tool orchestration boundaries."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from src.runtime import build_default_execution_bus, make_execution_request
from src.runtime.contracts import ExecutionResult

logger = logging.getLogger(__name__)


def _resolve_effective_model(*, input_payload: Dict[str, Any], metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    resolved_metadata = dict(metadata or {})
    resolved_payload = dict(input_payload or {})
    kwargs = resolved_payload.get("kwargs")
    kwargs = kwargs if isinstance(kwargs, dict) else {}
    llm_kwargs = kwargs.get("llm_kwargs")
    llm_kwargs = llm_kwargs if isinstance(llm_kwargs, dict) else {}

    candidates = [
        resolved_metadata.get("resolved_model"),
        resolved_metadata.get("model"),
        resolved_payload.get("model"),
        kwargs.get("model"),
        kwargs.get("model_name"),
        llm_kwargs.get("model"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


async def _execute_with_bus(
    *,
    request_id: Optional[str],
    source_type: str,
    source_ref: str,
    execution_type: str,
    session_id: Optional[str],
    context_ref: Optional[Dict[str, Any]],
    input_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
    register_handler_type: Optional[str] = None,
    custom_handler: Optional[Callable[[Any], Any]] = None,
    execute_tool_func: Optional[Callable[..., Any]] = None,
    agent_id: Optional[str] = None,
) -> ExecutionResult:
    bus = build_default_execution_bus(execute_tool_func=execute_tool_func)
    if register_handler_type and custom_handler is not None:
        bus.register_handler(register_handler_type, custom_handler)
    request = make_execution_request(
        request_id=request_id,
        source_type=source_type,
        source_ref=source_ref,
        agent_id=agent_id,
        execution_type=execution_type,
        session_id=session_id,
        context_ref=dict(context_ref or {}) if context_ref is not None else None,
        input_payload=dict(input_payload or {}),
        metadata=dict(metadata or {}),
    )
    result = await bus.execute(request)

    if session_id and execution_type in {"task", "skill", "subagent", "event"}:
        try:
            from src.runtime.progressive_context import apply_progressive_context_after_turn
            effective_model = _resolve_effective_model(
                input_payload=dict(input_payload or {}),
                metadata=dict(metadata or {}),
            )

            await apply_progressive_context_after_turn(
                session_id=session_id,
                model=effective_model,
            )
        except Exception:
            logger.warning(
                "Best-effort progressive context commit failed",
                extra={"session_id": session_id, "execution_type": execution_type},
                exc_info=True,
            )

    return result


async def execute_chat_orchestration(
    *,
    request_id: str,
    session_id: str,
    source_ref: str,
    input_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
    chat_handler: Callable[[Any], Any],
    agent_id: Optional[str] = None,
) -> ExecutionResult:
    """Build and execute a chat ExecutionRequest through the default bus."""
    return await _execute_with_bus(
        request_id=request_id,
        source_type="chat",
        source_ref=source_ref,
        execution_type="chat",
        session_id=session_id,
        context_ref=None,
        metadata=dict(metadata or {}),
        input_payload=dict(input_payload or {}),
        register_handler_type="chat",
        custom_handler=chat_handler,
        agent_id=agent_id,
    )


async def execute_tool_or_task_orchestration(
    *,
    source_type: str,
    source_ref: str,
    execution_type: str,
    session_id: Optional[str],
    input_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
    execute_tool_func: Optional[Callable[..., Any]] = None,
    agent_id: Optional[str] = None,
) -> ExecutionResult:
    """Build and execute a tool/task request through the default bus."""
    return await _execute_with_bus(
        request_id=None,
        source_type=source_type,
        source_ref=source_ref,
        execution_type=execution_type,
        session_id=session_id,
        context_ref=None,
        input_payload=dict(input_payload or {}),
        metadata=dict(metadata or {}),
        execute_tool_func=execute_tool_func,
        agent_id=agent_id,
    )


async def execute_skill_orchestration(
    *,
    source_ref: str,
    session_id: Optional[str],
    input_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    custom_skill_handler: Optional[Callable[[Any], Any]] = None,
    agent_id: Optional[str] = None,
) -> ExecutionResult:
    """Execute a skill request through the runtime bus boundary."""
    return await _execute_with_bus(
        request_id=None,
        source_type="skill",
        source_ref=source_ref,
        execution_type="skill",
        session_id=session_id,
        context_ref=None,
        input_payload=input_payload,
        metadata=metadata,
        register_handler_type="skill" if custom_skill_handler is not None else None,
        custom_handler=custom_skill_handler,
        agent_id=agent_id,
    )


async def execute_subagent_orchestration(
    *,
    source_ref: str,
    session_id: Optional[str],
    input_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
) -> ExecutionResult:
    """Execute a subagent request through the runtime bus boundary."""
    return await _execute_with_bus(
        request_id=None,
        source_type="agent",
        source_ref=source_ref,
        execution_type="subagent",
        session_id=session_id,
        context_ref=None,
        input_payload=input_payload,
        metadata=metadata,
        agent_id=agent_id,
    )


async def execute_runtime_task_request(
    *,
    request_id: str,
    source_type: str,
    source_ref: str,
    execution_type: str,
    session_id: Optional[str],
    context_ref: Optional[Dict[str, Any]] = None,
    input_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
) -> ExecutionResult:
    """Execute a generic runtime request through the runtime bus boundary."""
    return await _execute_with_bus(
        request_id=request_id,
        source_type=source_type,
        source_ref=source_ref,
        execution_type=execution_type,
        session_id=session_id,
        context_ref=context_ref,
        input_payload=input_payload,
        metadata=metadata,
        agent_id=agent_id,
    )
