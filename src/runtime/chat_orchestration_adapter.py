"""Thin runtime bus adapters for chat/tool orchestration boundaries."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.runtime import build_default_execution_bus, make_execution_request
from src.runtime.contracts import ExecutionResult


async def execute_chat_orchestration(
    *,
    request_id: str,
    session_id: str,
    source_ref: str,
    input_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
    chat_handler: Callable[[Any], Any],
) -> ExecutionResult:
    """Build and execute a chat ExecutionRequest through the default bus."""
    bus = build_default_execution_bus(chat_handler=chat_handler)
    execution_request = make_execution_request(
        request_id=request_id,
        source_type="chat",
        source_ref=source_ref,
        execution_type="chat",
        session_id=session_id,
        input_payload=dict(input_payload or {}),
        metadata=dict(metadata or {}),
    )
    return await bus.execute(execution_request)


async def execute_tool_or_task_orchestration(
    *,
    source_type: str,
    source_ref: str,
    execution_type: str,
    session_id: Optional[str],
    input_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
    execute_tool_func: Optional[Callable[..., Any]] = None,
) -> ExecutionResult:
    """Build and execute a tool/task request through the default bus."""
    bus = build_default_execution_bus(execute_tool_func=execute_tool_func)
    request = make_execution_request(
        source_type=source_type,
        source_ref=source_ref,
        execution_type=execution_type,
        session_id=session_id,
        input_payload=dict(input_payload or {}),
        metadata=dict(metadata or {}),
    )
    return await bus.execute(request)
