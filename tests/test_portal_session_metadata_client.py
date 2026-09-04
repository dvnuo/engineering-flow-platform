import pytest
from uuid import uuid4

from src.runtime.contracts import make_execution_result
from src.runtime.portal_session_metadata_client import (
    build_session_metadata_payload,
    extract_session_metadata_publish_fields,
)


def test_build_session_metadata_payload_includes_supported_fields_only():
    payload = build_session_metadata_payload(
        last_execution_id="exec-1",
        latest_event_type="task.completed",
        latest_event_state="success",
        snapshot_version="v1",
        runtime_events=[{"event_type": "x"}],
        metadata={
            "group_id": "g-1",
            "current_task_id": "t-1",
            "source_type": "portal",
            "source_ref": "task-1",
            "portal_task_id": "task-1",
            "unknown": "drop-me",
        },
        pending_delegations=[{"delegation_id": "d-1"}],
    )

    assert payload["group_id"] == "g-1"
    assert payload["current_task_id"] == "t-1"
    assert payload["source_type"] == "portal"
    assert payload["source_ref"] == "task-1"
    assert payload["last_execution_id"] == "exec-1"
    assert payload["latest_event_type"] == "task.completed"
    assert payload["latest_event_state"] == "success"
    assert payload["snapshot_version"] == "v1"
    assert "runtime_events_json" in payload
    assert "pending_delegations_json" in payload
    assert "metadata_json" in payload


def test_build_session_metadata_payload_includes_pending_delegations_when_non_empty():
    payload = build_session_metadata_payload(
        last_execution_id="exec-1",
        latest_event_type="task.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={},
        pending_delegations=[{"delegation_id": "d-1"}],
    )

    assert payload["pending_delegations_json"] == '[{"delegation_id": "d-1"}]'


def test_build_session_metadata_payload_includes_pending_delegations_when_empty_list():
    payload = build_session_metadata_payload(
        last_execution_id="exec-1",
        latest_event_type="task.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={},
        pending_delegations=[],
    )

    assert "pending_delegations_json" in payload
    assert payload["pending_delegations_json"] == "[]"


def test_build_session_metadata_payload_omits_pending_delegations_when_none():
    payload = build_session_metadata_payload(
        last_execution_id="exec-1",
        latest_event_type="task.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={},
        pending_delegations=None,
    )

    assert "pending_delegations_json" not in payload


def test_build_session_metadata_payload_canonical_keys_take_precedence_over_portal_aliases():
    payload = build_session_metadata_payload(
        last_execution_id="exec-3",
        latest_event_type="task.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={
            "group_id": "g-canonical",
            "portal_group_id": "g-legacy",
            "current_task_id": "t-canonical",
            "portal_task_id": "t-legacy",
            "current_delegation_id": "d-canonical",
            "portal_delegation_id": "d-legacy",
            "current_coordination_run_id": "c-canonical",
            "portal_coordination_run_id": "c-legacy",
            "source_ref": "src-canonical",
            "task_id": "task-fallback",
        },
    )

    assert payload["group_id"] == "g-canonical"
    assert payload["current_task_id"] == "t-canonical"
    assert payload["current_delegation_id"] == "d-canonical"
    assert payload["current_coordination_run_id"] == "c-canonical"
    assert payload["source_ref"] == "src-canonical"


def test_build_session_metadata_payload_supports_portal_alias_fallbacks():
    payload = build_session_metadata_payload(
        last_execution_id="exec-4",
        latest_event_type="task.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={
            "portal_group_id": "g-legacy",
            "portal_task_id": "t-legacy",
            "portal_delegation_id": "d-legacy",
            "portal_coordination_run_id": "c-legacy",
        },
    )

    assert payload["group_id"] == "g-legacy"
    assert payload["current_task_id"] == "t-legacy"
    assert payload["current_delegation_id"] == "d-legacy"
    assert payload["current_coordination_run_id"] == "c-legacy"
    assert payload["source_ref"] == "t-legacy"


def test_build_session_metadata_payload_stringifies_id_like_top_level_fields_for_canonical_and_alias_values():
    canonical_coordination = uuid4()
    alias_delegation = uuid4()
    alias_source = uuid4()

    payload = build_session_metadata_payload(
        last_execution_id="exec-5",
        latest_event_type="task.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={
            "group_id": 123,
            "portal_group_id": "legacy-group",
            "current_task_id": 456,
            "portal_task_id": 789,
            "portal_delegation_id": alias_delegation,
            "current_coordination_run_id": canonical_coordination,
            "portal_coordination_run_id": "legacy-coordination",
            "source_ref": alias_source,
            "task_id": "task-fallback",
        },
    )

    assert payload["group_id"] == "123"
    assert payload["current_task_id"] == "456"
    assert payload["current_delegation_id"] == str(alias_delegation)
    assert payload["current_coordination_run_id"] == str(canonical_coordination)
    assert payload["source_ref"] == str(alias_source)


def test_extract_session_metadata_publish_fields_prefers_latest_runtime_event():
    result = make_execution_result(
        request_id="exec-2",
        status="success",
        output_payload={},
        artifacts={"recovery": {"snapshot_version": "snap-1"}},
        runtime_events=[
            {"event_type": "task.started", "state": "started"},
            {"event_type": "task.completed", "state": "completed"},
        ],
    )

    extracted = extract_session_metadata_publish_fields(
        result,
        metadata={"portal_task_id": "pt-1"},
        default_event_type="task.completed",
        default_state="success",
    )

    assert extracted["last_execution_id"] == "exec-2"
    assert extracted["latest_event_type"] == "task.completed"
    assert extracted["latest_event_state"] == "success"
    assert extracted["snapshot_version"] == "snap-1"
    assert extracted["metadata"]["portal_task_id"] == "pt-1"


def test_build_session_metadata_payload_preserves_context_preview_keys():
    payload = build_session_metadata_payload(
        last_execution_id="exec-ctx",
        latest_event_type="chat.completed",
        latest_event_state="success",
        snapshot_version="phase3.v1",
        runtime_events=[],
        metadata={
            "context_compaction_level": "micro",
            "context_objective_preview": "Ship progressive context",
            "context_summary_preview": "Conversation compacted",
            "context_next_step_preview": "Proceed with verification",
        },
    )

    metadata_json = payload.get("metadata_json", "")
    assert "context_compaction_level" in metadata_json
    assert "context_objective_preview" in metadata_json
    assert "context_summary_preview" in metadata_json
    assert "context_next_step_preview" in metadata_json


def test_build_session_metadata_payload_preserves_context_budget_preview_keys():
    payload = build_session_metadata_payload(
        last_execution_id="exec-ctx-budget",
        latest_event_type="chat.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={
            "context_usage_percent": 42.0,
            "context_estimated_tokens": 4200,
            "context_window_tokens": 128_000,
            "context_next_compaction_action": "approaching_micro_compaction",
            "context_next_pruning_policy": "Approaching micro-compaction...",
            "context_tokens_until_soft_threshold": 1200,
            "context_tokens_until_hard_threshold": 5600,
            "context_state": {"budget": {"usage_percent": 42.0}},
        },
    )

    metadata_json = payload.get("metadata_json", "")
    assert "context_usage_percent" in metadata_json
    assert "context_estimated_tokens" in metadata_json
    assert "context_window_tokens" in metadata_json
    assert "context_next_compaction_action" in metadata_json
    assert "context_next_pruning_policy" in metadata_json
    assert "context_tokens_until_soft_threshold" in metadata_json
    assert "context_tokens_until_hard_threshold" in metadata_json
    assert "context_state" in metadata_json


def test_build_session_metadata_payload_preserves_active_skill_preview_keys():
    payload = build_session_metadata_payload(
        last_execution_id="exec-skill",
        latest_event_type="chat.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={
            "active_skill_name": "review-pull-request",
            "active_skill_status": "active",
            "active_skill_goal": "Review PR #12",
            "active_skill_hash": "abc123",
            "active_skill_turn_count": 2,
            "active_skill_activation_reason": "continued",
            "active_skill_tool_policy_declared": True,
            "active_skill_session": {"should": "not-pass"},
        },
    )

    metadata_json = payload.get("metadata_json", "")
    assert "active_skill_name" in metadata_json
    assert "active_skill_status" in metadata_json
    assert "active_skill_goal" in metadata_json
    assert "active_skill_hash" in metadata_json
    assert "active_skill_turn_count" in metadata_json
    assert "active_skill_activation_reason" in metadata_json
    assert "active_skill_tool_policy_declared" in metadata_json
    assert "active_skill_session" not in metadata_json


@pytest.mark.parametrize("status", ["waiting_for_question", "waiting_for_permission"])
def test_a_run_parked_on_the_member_is_published_as_blocked_not_success(status):
    """A run that stops to ask has not finished and has not failed.

    Neither waiting status matched any branch, so both fell through to the
    caller's fallback -- and every chat path passes `default_state="success"`.
    Portal was told the run completed fine, then found no response text in it,
    and reported `completion_state: incomplete` for ordinary behaviour.
    """
    result = make_execution_result(
        request_id="exec-parked",
        status=status,
        output_payload={},
        artifacts={},
        runtime_events=[],
    )

    extracted = extract_session_metadata_publish_fields(
        result,
        metadata={},
        default_event_type="chat.completed",
        default_state="success",
    )

    assert extracted["latest_event_state"] == "blocked"


def test_an_unrecognised_status_still_falls_back():
    # The fallback is what carries every status this does not name; parking is
    # now named, and nothing else changed.
    result = make_execution_result(
        request_id="exec-odd",
        status="something_new",
        output_payload={},
        artifacts={},
        runtime_events=[],
    )

    extracted = extract_session_metadata_publish_fields(
        result,
        metadata={},
        default_event_type="chat.completed",
        default_state="success",
    )

    assert extracted["latest_event_state"] == "success"
