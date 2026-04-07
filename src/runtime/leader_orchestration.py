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


def aggregate_delegation_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized_items = [item for item in results if isinstance(item, dict)]
    blockers: List[str] = []
    summaries: List[str] = []
    next_recommendations: List[str] = []
    artifacts: List[Dict[str, Any]] = []
    status_by_assignee: Dict[str, str] = {}

    for item in normalized_items:
        result_payload = item.get("result") if isinstance(item.get("result"), dict) else item
        delegation_result = (
            result_payload.get("delegation_result")
            if isinstance(result_payload, dict) and isinstance(result_payload.get("delegation_result"), dict)
            else result_payload
            if isinstance(result_payload, dict)
            else {}
        )
        assignee = str(
            delegation_result.get("assignee_agent_id")
            or result_payload.get("assignee_agent_id")
            or item.get("assignee_agent_id")
            or ""
        ).strip()
        raw_status = str(delegation_result.get("status") or result_payload.get("status") or "").strip().lower()
        normalized_status = "done" if raw_status in {"done", "completed", "success"} else raw_status or "unknown"
        if assignee:
            status_by_assignee[assignee] = normalized_status
        for blocker in delegation_result.get("blockers") or result_payload.get("blockers") or []:
            if blocker:
                blockers.append(str(blocker))
        summary = delegation_result.get("summary") or result_payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            summaries.append(summary.strip())
        next_rec = delegation_result.get("next_recommendation") or result_payload.get("next_recommendation")
        if isinstance(next_rec, str) and next_rec.strip():
            next_recommendations.append(next_rec.strip())
        raw_artifacts = delegation_result.get("artifacts") or result_payload.get("artifacts") or []
        if isinstance(raw_artifacts, list):
            artifacts.extend([art for art in raw_artifacts if isinstance(art, dict)])

    all_done = bool(status_by_assignee) and all(status == "done" for status in status_by_assignee.values())
    has_blockers = bool(blockers)
    return {
        "all_done": all_done,
        "has_blockers": has_blockers,
        "blockers": blockers,
        "summaries": summaries,
        "next_recommendations": next_recommendations,
        "artifacts": artifacts,
        "status_by_assignee": status_by_assignee,
    }


def evaluate_completion_criteria(completion_criteria: Dict[str, Any] | None, aggregate: Dict[str, Any]) -> Dict[str, Any]:
    criteria = completion_criteria if isinstance(completion_criteria, dict) else {}
    mode = str(criteria.get("mode") or "all_done").strip() or "all_done"
    if aggregate.get("has_blockers"):
        return {"is_complete": False, "reason": "blockers_present", "mode": mode}
    if mode == "all_done":
        return {"is_complete": bool(aggregate.get("all_done")), "reason": "all_done_check", "mode": mode}
    return {"is_complete": bool(aggregate.get("all_done")), "reason": "unsupported_mode_fallback_all_done", "mode": mode}


async def run_delegation_cycle(
    *,
    group_id: str,
    leader_agent_id: str,
    leader_session_id: str,
    coordination_run_id: str | None = None,
    round_index: int | None = None,
    tasks: List[Dict[str, Any]] | None = None,
    prior_results: List[Dict[str, Any]] | None = None,
    completion_criteria: Dict[str, Any] | None = None,
    request_id: str | None = None,
) -> Dict[str, Any]:
    normalized_round = int(round_index if isinstance(round_index, int) else 0)
    run_id = str(coordination_run_id or f"coord-{request_id or leader_session_id}-{normalized_round}").strip()
    normalized_tasks = list(tasks or [])
    enriched_tasks = []
    for item in normalized_tasks:
        if isinstance(item, dict):
            enriched_tasks.append({**item, "coordination_run_id": run_id, "round_index": normalized_round, "leader_session_id": leader_session_id})
    dispatch_result = {"success": True, "created": 0, "failed": 0, "items": []}
    if enriched_tasks:
        dispatch_result = await dispatch_task_breakdown_as_delegations(
            group_id=group_id,
            leader_agent_id=leader_agent_id,
            leader_session_id=leader_session_id,
            tasks=enriched_tasks,
        )
    combined_results = list(prior_results or []) + list(dispatch_result.get("items") or [])
    aggregate = aggregate_delegation_results(combined_results)
    completion_eval = evaluate_completion_criteria(completion_criteria, aggregate)
    if aggregate.get("has_blockers"):
        next_action = "blocked"
    elif completion_eval.get("is_complete"):
        next_action = "complete"
    else:
        next_action = "continue"
    return {
        "success": bool(dispatch_result.get("success", True)),
        "coordination_run_id": run_id,
        "round_index": normalized_round,
        "created": int(dispatch_result.get("created", 0)),
        "failed": int(dispatch_result.get("failed", 0)),
        "items": list(dispatch_result.get("items", [])),
        "aggregate": aggregate,
        "is_complete": bool(completion_eval.get("is_complete")),
        "completion_criteria": completion_criteria or {"mode": "all_done"},
        "prior_results": list(prior_results or []),
        "next_action": next_action,
    }
