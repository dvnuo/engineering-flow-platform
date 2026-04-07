"""Small runtime helper for leader task-breakdown delegation dispatch."""

from __future__ import annotations

from typing import Any, Dict, List

from src.runtime.adapter_executor import execute_adapter_action
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
    run_status = str(aggregate.get("run_status") or "").strip().lower()
    if run_status == "blocked":
        return {"is_complete": False, "reason": "run_blocked", "mode": mode}
    if run_status in {"queued", "running"}:
        return {"is_complete": False, "reason": "work_in_progress", "mode": mode}
    if run_status == "done":
        return {"is_complete": True, "reason": "run_done", "mode": mode}
    if run_status == "failed":
        return {"is_complete": False, "reason": "failed_terminal", "mode": mode}
    status_counts = aggregate.get("status_counts") if isinstance(aggregate.get("status_counts"), dict) else {}
    if int(status_counts.get("queued", 0)) > 0 or int(status_counts.get("running", 0)) > 0:
        return {"is_complete": False, "reason": "work_in_progress", "mode": mode}
    if bool(aggregate.get("all_terminal")) and bool(aggregate.get("all_done")):
        return {"is_complete": True, "reason": "run_terminal_done", "mode": mode}
    if bool(aggregate.get("all_terminal")) and not bool(aggregate.get("all_done")):
        return {"is_complete": False, "reason": "failed_terminal", "mode": mode}
    status_values = list((aggregate.get("status_by_assignee") or {}).values())
    if any(status in {"queued", "running", "in_progress"} for status in status_values):
        return {"is_complete": False, "reason": "work_in_progress", "mode": mode}
    if any(status in {"failed", "error", "blocked"} for status in status_values):
        return {"is_complete": False, "reason": "failed_terminal", "mode": mode}
    if mode == "all_done":
        return {"is_complete": bool(aggregate.get("all_done")), "reason": "all_done_check", "mode": mode}
    return {"is_complete": bool(aggregate.get("all_done")), "reason": "unsupported_mode_fallback_all_done", "mode": mode}


async def load_coordination_run_state(*, group_id: str, coordination_run_id: str) -> Dict[str, Any]:
    normalized_run_id = str(coordination_run_id or "").strip()
    run_outcome = await execute_adapter_action(
        "adapter:portal:get_coordination_run",
        {"coordination_run_id": normalized_run_id},
    )
    run_result_payload = run_outcome.get("result")
    run_record = run_result_payload if isinstance(run_result_payload, dict) else {}

    run_summary = run_record.get("summary") if isinstance(run_record.get("summary"), dict) else {}
    delegation_outcome = {"success": False, "result": None}
    result_payload = None
    if not run_record or not run_summary:
        delegation_outcome = await execute_adapter_action("adapter:portal:list_group_delegations", {"group_id": group_id})
        result_payload = delegation_outcome.get("result")

    if isinstance(result_payload, dict):
        delegations = result_payload.get("delegations") or result_payload.get("items") or []
    elif isinstance(result_payload, list):
        delegations = result_payload
    else:
        delegations = []
    matched: List[Dict[str, Any]] = []
    rounds: List[int] = []
    status_counts: Dict[str, int] = {"queued": 0, "running": 0, "done": 0, "failed": 0, "other": 0}
    has_blockers = False
    for item in delegations:
        if not isinstance(item, dict):
            continue
        if str(item.get("coordination_run_id") or "").strip() != normalized_run_id:
            continue
        matched.append(item)
        round_index_raw = item.get("round_index")
        round_index = round_index_raw if isinstance(round_index_raw, int) and round_index_raw > 0 else 1
        rounds.append(round_index)
        raw_status = str(item.get("status") or "").strip().lower()
        normalized_status = "done" if raw_status in {"done", "completed", "success"} else raw_status
        if normalized_status in status_counts:
            status_counts[normalized_status] += 1
        elif normalized_status in {"in_progress"}:
            status_counts["running"] += 1
        else:
            status_counts["other"] += 1
        blockers = item.get("blockers") or item.get("result", {}).get("blockers") if isinstance(item.get("result"), dict) else item.get("blockers")
        if isinstance(blockers, list) and blockers:
            has_blockers = True
    total = len(matched)
    all_terminal = status_counts["queued"] == 0 and status_counts["running"] == 0
    all_done = total > 0 and status_counts["done"] == total and status_counts["failed"] == 0 and status_counts["other"] == 0

    run_status = str(run_record.get("status") or "").strip().lower()
    completed_at = run_record.get("completed_at")
    run_rounds = run_record.get("rounds") if isinstance(run_record.get("rounds"), list) else []
    if run_rounds:
        for round_value in run_rounds:
            if isinstance(round_value, int) and round_value > 0:
                rounds.append(round_value)
    run_delegations = run_record.get("delegations") if isinstance(run_record.get("delegations"), list) else []
    if run_delegations:
        matched = [item for item in run_delegations if isinstance(item, dict)]
    if run_summary:
        summary_counts = run_summary.get("status_counts") if isinstance(run_summary.get("status_counts"), dict) else None
        if summary_counts:
            for key in ("queued", "running", "done", "failed", "other"):
                status_counts[key] = int(summary_counts.get(key, status_counts.get(key, 0)))

    if run_status == "done":
        all_terminal = True
        all_done = True
    elif run_status == "failed":
        all_terminal = True
        all_done = False
    elif run_status in {"running", "queued"}:
        all_terminal = False
    if run_status == "blocked":
        has_blockers = True

    return {
        "coordination_run_id": normalized_run_id,
        "status": run_status or ("done" if all_done else "running" if not all_terminal else "failed"),
        "summary": run_summary,
        "rounds": sorted(set(rounds)),
        "delegations": matched,
        "status_counts": status_counts,
        "latest_round_index": max(rounds) if rounds else 1,
        "all_terminal": all_terminal,
        "all_done": all_done,
        "has_blockers": has_blockers,
        "completed_at": completed_at,
    }


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
    normalized_round = int(round_index if isinstance(round_index, int) else 1)
    if normalized_round < 1:
        normalized_round = 1
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
    run_state: Dict[str, Any] = {
        "coordination_run_id": run_id,
        "rounds": [normalized_round],
        "delegations": [],
        "status_counts": {},
        "latest_round_index": normalized_round,
        "all_terminal": True,
        "all_done": False,
        "has_blockers": False,
    }
    run_state = await load_coordination_run_state(group_id=group_id, coordination_run_id=run_id)

    combined_results = list(prior_results or []) + list(dispatch_result.get("items") or []) + list(run_state.get("delegations") or [])
    aggregate = aggregate_delegation_results(combined_results)
    aggregate = {
        **aggregate,
        "run_status": run_state.get("status"),
        "status_counts": run_state.get("status_counts", {}),
        "all_terminal": bool(run_state.get("all_terminal")),
        "all_done": bool(run_state.get("all_done")),
        "has_blockers": bool(aggregate.get("has_blockers")) or bool(run_state.get("has_blockers")),
    }
    completion_eval = evaluate_completion_criteria(completion_criteria, aggregate)
    run_status = str(run_state.get("status") or "").strip().lower()
    run_active = run_status in {"queued", "running"} or not bool(run_state.get("all_terminal"))
    if aggregate.get("has_blockers") or run_status == "blocked":
        next_action = "blocked"
    elif completion_eval.get("is_complete"):
        next_action = "complete"
    elif run_active:
        next_action = "continue"
    else:
        next_action = "review"
    leader_summary_status = "blocked" if aggregate.get("has_blockers") else "complete" if completion_eval.get("is_complete") else "in_progress"
    return {
        "success": bool(dispatch_result.get("success", True)),
        "coordination_run_id": run_id,
        "round_index": normalized_round,
        "created": int(dispatch_result.get("created", 0)),
        "failed": int(dispatch_result.get("failed", 0)),
        "items": list(dispatch_result.get("items", [])),
        "aggregate": aggregate,
        "run_state": run_state,
        "is_complete": bool(completion_eval.get("is_complete")),
        "completion_criteria": completion_criteria or {"mode": "all_done"},
        "prior_results": list(prior_results or []),
        "next_action": next_action,
        "leader_summary": {
            "status": leader_summary_status,
            "latest_round_index": run_state.get("latest_round_index", normalized_round),
            "run_status": run_state.get("status"),
            "status_counts": run_state.get("status_counts", {}),
            "completed_at": run_state.get("completed_at"),
            "blockers": aggregate.get("blockers", []),
            "next_recommendations": aggregate.get("next_recommendations", []),
            "summary": run_state.get("summary", {}),
        },
    }
