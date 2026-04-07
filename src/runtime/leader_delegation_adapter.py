"""Small runtime helper for leader-driven Portal delegation creation."""

from __future__ import annotations

from typing import Any, Dict

from src.runtime.adapter_executor import execute_adapter_action


REQUIRED_DELEGATION_FIELDS = ("group_id", "leader_agent_id", "assignee_agent_id", "objective", "visibility")


async def create_portal_delegation_from_runtime(payload: Dict[str, Any]) -> Dict[str, Any]:
    request_payload = dict(payload or {})
    missing = [key for key in REQUIRED_DELEGATION_FIELDS if not str(request_payload.get(key) or "").strip()]
    if missing:
        return {
            "success": False,
            "delegation_id": None,
            "error": f"Missing required fields: {', '.join(missing)}",
            "result": None,
        }

    outcome = await execute_adapter_action("adapter:portal:create_delegation", request_payload)
    result_payload = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    delegation_id = result_payload.get("delegation_id") or result_payload.get("id")
    return {
        "success": bool(outcome.get("success")),
        "delegation_id": delegation_id,
        "error": outcome.get("error"),
        "result": outcome.get("result"),
    }

