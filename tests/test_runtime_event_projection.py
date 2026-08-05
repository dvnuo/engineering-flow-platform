from src.efp_runtime.events import RuntimeEvent
from src.gateway.runtime_event_projection import RuntimeEventProjector


def test_projector_maps_run_start_to_session_next_step_started():
    projector = RuntimeEventProjector(
        request_id="req-1",
        agent_id="agent-1",
        agent_name="Agent One",
        model="gpt-5.4",
    )
    event = RuntimeEvent(
        type="run_start",
        session_id="s-1",
        payload={"run_id": "run-1", "max_iterations": 4},
        created_at="2026-06-02T01:02:03Z",
    )

    projected = projector.project(event)

    assert len(projected) == 1
    payload = projected[0]
    assert payload["type"] == "session.next.step.started"
    assert payload["event_type"] == "session.next.step.started"
    assert payload["engine"] == "efp-native"
    assert payload["session_id"] == "s-1"
    assert payload["request_id"] == "req-1"
    assert payload["agent_id"] == "agent-1"
    assert payload["data"]["model"] == "gpt-5.4"
    assert payload["data"]["sessionID"] == "s-1"
    assert payload["properties"]["run_id"] == "run-1"


def test_projector_emits_text_started_before_first_delta_and_ended_on_finish():
    projector = RuntimeEventProjector(request_id="req-1", model="gpt-5.4")

    projected = projector.project(
        RuntimeEvent(
            type="llm.text_delta",
            session_id="s-1",
            part_id="part-1",
            payload={"run_id": "run-1", "iteration": 1, "delta": "hello"},
            created_at="2026-06-02T01:02:03Z",
        )
    )

    assert [event["type"] for event in projected] == [
        "session.next.text.started",
        "session.next.text.delta",
    ]
    assert projected[1]["data"]["delta"] == "hello"
    assert projected[1]["properties"]["content_delta"] == "hello"

    finish = projector.project(
        RuntimeEvent(
            type="llm.step_finish",
            session_id="s-1",
            payload={"run_id": "run-1", "iteration": 1},
            created_at="2026-06-02T01:02:04Z",
        )
    )
    assert [event["type"] for event in finish] == ["session.next.text.ended"]


def test_projector_hashes_long_tool_call_id_and_maps_tool_lifecycle():
    projector = RuntimeEventProjector(request_id="req-1")
    long_call_id = "call_" + ("very_long_tool_call_id_" * 8)

    input_events = projector.project(
        RuntimeEvent(
            type="llm.tool_call_delta",
            session_id="s-1",
            payload={
                "llm_event_type": "tool_input_delta",
                "tool_call_id": long_call_id,
                "tool_name": "search",
                "arguments_delta": '{"token":"secret-value","query":"efp"}',
            },
            created_at="2026-06-02T01:02:03Z",
        )
    )

    assert [event["type"] for event in input_events] == [
        "session.next.tool.input.started",
        "session.next.tool.input.delta",
    ]
    call_id = input_events[1]["data"]["tool_call_id"]
    assert len(call_id) <= 64
    assert call_id.startswith("call_")
    assert long_call_id not in input_events[1]["id"]
    assert "secret-value" not in input_events[1]["data"]["arguments_preview"]

    done = projector.project(
        RuntimeEvent(
            type="llm.tool_call_done",
            session_id="s-1",
            payload={"tool_call_id": long_call_id, "tool_name": "search", "arguments": {"query": "efp"}},
            created_at="2026-06-02T01:02:04Z",
        )
    )
    assert done[0]["type"] == "session.next.tool.called"
    assert done[0]["data"]["tool_name"] == "search"


def test_projector_maps_permission_and_run_finish_usage_aliases():
    projector = RuntimeEventProjector(request_id="req-1", agent_id="agent-1")

    permission = projector.project(
        RuntimeEvent(
            type="tool.permission_requested",
            session_id="s-1",
            payload={
                "tool_call_id": "call-1",
                "tool_name": "write_file",
                "permission_request": {"id": "perm-1", "action": "ask", "reason": "needs approval"},
            },
            created_at="2026-06-02T01:02:03Z",
        )
    )[0]
    assert permission["type"] == "permission.requested"
    assert permission["permission_request"]["id"] == "perm-1"
    assert permission["data"]["permission_request"]["id"] == "perm-1"

    finish = projector.project(
        RuntimeEvent(
            type="run_finish",
            session_id="s-1",
            payload={"status": "completed", "iterations": 1, "usage": {"total_tokens": 12, "estimated_cost": 0.01}},
            created_at="2026-06-02T01:02:04Z",
        )
    )[-1]
    assert finish["type"] == "session.next.step.ended"
    assert finish["state"] == "success"
    assert finish["data"]["tokens"] == 12
    assert finish["data"]["cost"] == 0.01


def test_projector_maps_new_llm_error_and_finish_events():
    projector = RuntimeEventProjector(request_id="req-1")

    provider_error = projector.project(
        RuntimeEvent(
            type="llm.provider_error",
            session_id="s-1",
            payload={
                "run_id": "run-1",
                "iteration": 1,
                "error": "rate_limit_exceeded: Slow down",
                "code": "rate_limit_exceeded",
                "retryable": True,
            },
            created_at="2026-06-02T01:02:06Z",
        )
    )
    tool_error = projector.project(
        RuntimeEvent(
            type="llm.tool_error",
            session_id="s-1",
            payload={
                "tool_call_id": "call-1",
                "tool_name": "search",
                "error": "tool failed",
            },
            created_at="2026-06-02T01:02:07Z",
        )
    )

    assert provider_error[0]["type"] == "session.next.step.failed"
    assert provider_error[0]["data"]["code"] == "rate_limit_exceeded"
    assert provider_error[0]["data"]["retryable"] is True
    assert tool_error[0]["type"] == "session.next.tool.failed"
    assert tool_error[0]["data"]["tool_name"] == "search"

    finish = projector.project(
        RuntimeEvent(
            type="llm.finish",
            session_id="s-1",
            payload={"run_id": "run-1", "iteration": 1},
            created_at="2026-06-02T01:02:08Z",
        )
    )
    assert finish == []


def test_projector_distinguishes_request_compaction_from_stored_rewrites():
    """Both compaction kinds render the same, but must stay tellable apart.

    ``session_compacted`` means the stored session was rewritten on disk;
    ``request_compacted`` means only the outgoing request was trimmed. They
    share a UI event so the timeline keeps rendering both, so the projected
    ``stored``/``scope`` fields are the only signal a consumer has for whether
    anything on disk changed.
    """

    projector = RuntimeEventProjector(request_id="req-1", model="gpt-5.6-sol")

    request_only = projector.project(
        RuntimeEvent(
            type="request_compacted",
            session_id="s-1",
            payload={
                "run_id": "run-1",
                "iteration": 3,
                "trigger": "context_budget",
                "stored": False,
                "scope": "request",
                "max_chars": 1_568_000,
                "compacted_message_count": 9,
                "compacted_chars": 840_000,
                "kept_chars": 1_050_000,
            },
        )
    )
    stored = projector.project(
        RuntimeEvent(
            type="session_compacted",
            session_id="s-1",
            payload={
                "run_id": "run-1",
                "trigger": "context_budget",
                "stored": True,
                "scope": "session",
                "stored_message_count": 7,
            },
        )
    )

    assert len(request_only) == 1
    assert len(stored) == 1
    # Same UI event, so the Portal timeline needs no change...
    assert request_only[0]["type"] == "session.next.compaction.ended"
    assert stored[0]["type"] == "session.next.compaction.ended"
    # ...but the on-disk distinction survives projection.
    assert request_only[0]["data"]["stored"] is False
    assert request_only[0]["data"]["scope"] == "request"
    # Both emitters spread the compaction counters flat, so the projection has
    # to read them flat too -- otherwise the card reaches the UI with no
    # numbers and the user cannot tell how much context was dropped.
    assert request_only[0]["data"]["max_chars"] == 1_568_000
    assert request_only[0]["data"]["compacted_message_count"] == 9
    assert request_only[0]["data"]["compacted_chars"] == 840_000
    assert request_only[0]["data"]["kept_chars"] == 1_050_000
    assert stored[0]["data"]["stored"] is True
    assert stored[0]["data"]["scope"] == "session"
    assert stored[0]["data"]["stored_message_count"] == 7
