import pytest

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
