import asyncio
import time
from datetime import datetime, timezone

from src.gateway.chat_run_registry import (
    RETAINED_EVENT_TAIL_ITEMS,
    ChatRunRegistry,
)


def test_chat_run_registry_tracks_events_and_completion():
    registry = ChatRunRegistry()
    record = registry.start(session_id="s1", request_id="r1")

    registry.record_event("r1", {"created_at": "2026-06-17T00:00:00Z"})
    registry.complete("r1", {"ok": True, "response": "done"})

    payload = record.to_payload()
    assert payload["state"] == "completed"
    assert payload["terminal"] is True
    assert payload["latest_event_seq"] == 1
    assert payload["final_payload"]["response"] == "done"


def test_chat_run_registry_cancel_is_not_overwritten_by_late_failure():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")

    assert registry.cancel("r1") is True
    registry.fail("r1", {"error": "late"})

    payload = registry.get("r1").to_payload()
    assert payload["state"] == "cancelled"
    assert payload["terminal"] is True


def test_chat_run_registry_prunes_only_terminal_records():
    registry = ChatRunRegistry(max_records=2)
    registry.start(session_id="s1", request_id="old")
    registry.complete("old", {"ok": True})
    registry.start(session_id="s1", request_id="running")
    registry.start(session_id="s1", request_id="new")

    assert registry.get("old") is None
    assert registry.get("running") is not None
    assert registry.get("new") is not None


def test_chat_run_registry_compacts_retained_event_streams():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")
    events = [{"type": "llm.text_delta", "payload": {"delta": str(i)}} for i in range(500)]

    registry.complete(
        "r1",
        {"ok": True, "response": "done", "events": events, "runtime_events": events},
    )

    final = registry.get("r1").to_payload()["final_payload"]
    assert final["response"] == "done"
    assert len(final["runtime_events"]) == RETAINED_EVENT_TAIL_ITEMS
    assert final["runtime_events"][-1] == events[-1]
    assert final["runtime_events_count"] == 500
    assert final["runtime_events_truncated"] is True
    assert len(final["events"]) == RETAINED_EVENT_TAIL_ITEMS


def test_chat_run_registry_keeps_small_event_lists_intact():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")
    events = [{"type": "llm.text_delta", "payload": {"delta": "x"}}]

    registry.complete("r1", {"ok": True, "response": "done", "runtime_events": events})

    final = registry.get("r1").to_payload()["final_payload"]
    assert final["runtime_events"] == events
    assert "runtime_events_count" not in final
    assert "runtime_events_truncated" not in final


def test_chat_run_registry_releases_task_reference_on_terminal():
    async def scenario():
        registry = ChatRunRegistry()
        record = registry.start(session_id="s1", request_id="r1")

        async def _noop():
            return {"ok": True}

        task = asyncio.create_task(_noop())
        registry.attach_task("r1", task)
        await task
        assert record.task is task

        registry.complete("r1", {"ok": True, "response": "done"})
        assert record.task is None

    asyncio.run(scenario())


def test_chat_run_registry_marks_stale_running_records_failed_and_prunable():
    registry = ChatRunRegistry(max_records=2, stale_running_seconds=3600)
    stuck = registry.start(session_id="s1", request_id="stuck")
    stuck.updated_at = "2000-01-01T00:00:00Z"

    registry.start(session_id="s1", request_id="new1")

    stuck_record = registry.get("stuck")
    assert stuck_record.state == "failed"
    assert stuck_record.terminal is True
    assert stuck_record.error_payload["error"] == "chat_run_stale"

    registry.start(session_id="s1", request_id="new2")
    assert registry.get("stuck") is None
    assert registry.get("new1") is not None
    assert registry.get("new2") is not None


def test_chat_run_registry_stale_check_handles_mixed_timestamp_formats():
    registry = ChatRunRegistry(max_records=10, stale_running_seconds=3600)
    fresh = registry.start(session_id="s1", request_id="fresh")
    fresh.updated_at = datetime.now(timezone.utc).isoformat()  # offset suffix, no 'Z'
    stale = registry.start(session_id="s1", request_id="stale")
    stale.updated_at = "2000-01-01T00:00:00+00:00"
    unparseable = registry.start(session_id="s1", request_id="weird")
    unparseable.updated_at = "not-a-timestamp"

    registry.start(session_id="s1", request_id="trigger")

    assert registry.get("fresh").state == "running"
    assert registry.get("stale").state == "failed"
    # Unparseable timestamps must not get a run killed.
    assert registry.get("weird").state == "running"


def test_chat_run_registry_numbers_each_event_and_journals_it():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")
    first = {"type": "session.next.step.started", "request_id": "r1"}
    second = {"type": "session.next.step.ended", "request_id": "r1"}

    assert registry.record_event("r1", first) == 1
    assert registry.record_event("r1", second) == 2

    # The number travels on the event itself, to every place it is sent.
    assert first["run_seq"] == 1
    assert second["run_seq"] == 2
    journal = registry.journal_for("r1")
    assert [event["type"] for event, _ts in journal.records_after(0)] == [
        "session.next.step.started",
        "session.next.step.ended",
    ]
    assert [event["run_seq"] for event, _ts in journal.records_after(1)] == [2]


def test_chat_run_registry_leaves_an_event_alone_when_the_run_is_over_or_unknown():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")
    registry.complete("r1", {"ok": True})
    late = {"type": "session.next.step.started"}
    stray = {"type": "session.next.step.started"}

    assert registry.record_event("r1", late) == 0
    assert registry.record_event("missing", stray) == 0

    assert "run_seq" not in late
    assert "run_seq" not in stray
    assert registry.get("r1").latest_event_seq == 0
    assert registry.journal_for("r1").records_after(0) == []


def test_chat_run_registry_keeps_a_finished_runs_journal_for_a_while():
    registry = ChatRunRegistry()
    record = registry.start(session_id="s1", request_id="r1")
    registry.record_event("r1", {"type": "session.next.step.started"})
    assert record.journal_expires_at == 0

    registry.complete("r1", {"ok": True})

    # A viewer still catching up on the last events finds them.
    assert registry.journal_for("r1").last_seq == 1

    record.journal_expires_at = time.monotonic() - 1
    assert registry.journal_for("r1") is None
    assert record.journal is None


def test_chat_run_registry_drops_expired_journals_when_a_run_starts():
    registry = ChatRunRegistry()
    finished = registry.start(session_id="s1", request_id="finished")
    registry.fail("finished", {"error": "boom"})
    recent = registry.start(session_id="s1", request_id="recent")
    registry.cancel("recent")
    finished.journal_expires_at = time.monotonic() - 1

    running = registry.start(session_id="s1", request_id="next")

    assert finished.journal is None
    assert recent.journal is not None
    assert running.journal is not None


def test_chat_run_registry_drops_a_finished_runs_journal_on_a_timer():
    async def scenario():
        registry = ChatRunRegistry(journal_retention_seconds=0.01)
        record = registry.start(session_id="s1", request_id="r1")
        registry.record_event("r1", {"type": "session.next.step.started"})
        registry.complete("r1", {"ok": True})
        assert record.journal is not None

        await asyncio.sleep(0.2)
        assert record.journal is None
        assert registry.journal_for("r1") is None

    asyncio.run(scenario())


def test_chat_run_registry_journal_belongs_to_the_runs_session():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")

    assert registry.journal_for("r1") is not None
    assert registry.journal_for("r1", session_id="s1") is not None
    assert registry.journal_for("r1", session_id="other") is None
    assert registry.journal_for("missing") is None


def test_chat_run_registry_reports_the_run_in_flight_for_a_session():
    registry = ChatRunRegistry()
    assert registry.live_run_for_session("s1") is None

    record = registry.start(session_id="s1", request_id="r1")
    registry.record_event("r1", {"type": "session.next.step.started"})
    registry.record_event("r1", {"type": "session.next.step.ended"})

    assert registry.live_run_for_session("s1") == {
        "request_id": "r1",
        "state": "running",
        "started_at": record.started_at,
        "last_seq": 2,
    }
    assert registry.live_run_for_session("other") is None

    # A run being stopped is still in flight until it has ended.
    registry.request_cancel("r1")
    assert registry.live_run_for_session("s1")["state"] == "cancelling"

    registry.complete("r1", {"ok": True})
    assert registry.live_run_for_session("s1") is None
