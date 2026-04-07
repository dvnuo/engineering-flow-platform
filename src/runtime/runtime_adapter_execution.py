"""Helpers for routing runtime-internal adapter actions through ExecutionBus."""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.runtime.chat_orchestration_adapter import execute_runtime_task_request


async def execute_adapter_action_via_bus(
    action_id: str,
    kwargs: Dict[str, Any],
    *,
    source_type: str = "runtime",
    source_ref: str = "runtime.adapter_action_via_bus",
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    policy_profile_id: Optional[str] = None,
    context_ref: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = await execute_runtime_task_request(
        request_id=f"runtime-{action_id}",
        source_type=source_type,
        source_ref=source_ref,
        session_id=session_id,
        execution_type="task",
        context_ref=context_ref,
        input_payload={
            "task_type": "adapter_action_task",
            "action_id": action_id,
            "kwargs": dict(kwargs or {}),
        },
        metadata={"policy_profile_id": policy_profile_id, **dict(metadata or {})} if policy_profile_id else dict(metadata or {}),
    )
    output = result.output_payload if isinstance(result.output_payload, dict) else {}
    return {
        "success": result.status == "success" and bool(output.get("success", True)),
        "error": output.get("error") or output.get("reason"),
        "result": output.get("result"),
        "action_id": output.get("action_id") or action_id,
        "capability_id": output.get("capability_id") or action_id,
        "capability_type": output.get("capability_type") or "adapter_action",
        "runtime_events": list(result.runtime_events or []),
        "status": result.status,
        "reason": output.get("reason"),
    }
