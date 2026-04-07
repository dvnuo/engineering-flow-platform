"""Small runtime helper for leader task-breakdown delegation dispatch."""

from __future__ import annotations

from typing import Any, Dict, List

from src.runtime.leader_delegation_adapter import (
    create_specialist_delegation,
    create_task_agent_delegation,
    normalize_leader_delegation_request,
)


def build_delegation_requests_from_task_breakdown(
    *,
    group_id: str,
    leader_agent_id: str,
    leader_session_id: str,
    tasks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a non-empty list")
    built: List[Dict[str, Any]] = []
    for item in tasks:
        if not isinstance(item, dict):
            raise ValueError("each task breakdown item must be an object")
        payload = normalize_leader_delegation_request(
            {
                **item,
                "group_id": group_id,
                "leader_agent_id": leader_agent_id,
            }
        )
        payload["leader_session_id"] = leader_session_id
        built.append(payload)
    return built


async def dispatch_task_breakdown_as_delegations(
    *,
    group_id: str,
    leader_agent_id: str,
    leader_session_id: str,
    tasks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    requests = build_delegation_requests_from_task_breakdown(
        group_id=group_id,
        leader_agent_id=leader_agent_id,
        leader_session_id=leader_session_id,
        tasks=tasks,
    )
    items: List[Dict[str, Any]] = []
    created = 0
    failed = 0
    for payload in requests:
        agent_mode = str(payload.get("agent_mode") or "specialist").strip() or "specialist"
        if agent_mode == "task":
            result = await create_task_agent_delegation(payload)
        else:
            result = await create_specialist_delegation(payload)
        if result.get("success"):
            created += 1
        else:
            failed += 1
        items.append({"payload": payload, "result": result})
    return {
        "success": failed == 0,
        "created": created,
        "failed": failed,
        "items": items,
    }

