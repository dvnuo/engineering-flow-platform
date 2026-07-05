from src.runtime.runtime_task_tracker import RuntimeTaskTracker


def _compact(payload):
    compact = dict(payload)
    events = compact.get("runtime_events")
    if isinstance(events, list) and len(events) > 10:
        compact["runtime_events"] = events[-10:]
        compact["runtime_events_count"] = len(events)
        compact["runtime_events_truncated"] = True
    return compact


def _create_pending(tracker, task_id="t1"):
    return tracker.create_pending(
        task_id=task_id,
        request_id=f"req-{task_id}",
        task_type="chat",
        source="portal",
        session_id="s1",
        agent_id="a1",
        trace_id=None,
        portal_dispatch_id=None,
        portal_task_id=task_id,
    )


def _terminal_payload():
    events = [{"type": "llm.text_delta", "payload": {"delta": str(i)}} for i in range(50)]
    return {"ok": True, "status": "success", "runtime_events": events}


def test_mark_terminal_compacts_memory_and_keeps_full_payload_on_disk(tmp_path):
    tracker = RuntimeTaskTracker(storage_dir=tmp_path)
    tracker.configure_compaction(_compact)
    _create_pending(tracker)
    tracker.mark_running("t1")

    tracker.mark_terminal("t1", status="success", payload=_terminal_payload())

    record = tracker.get("t1")
    assert record.payload_compacted is True
    assert len(record.payload["runtime_events"]) == 10
    assert record.payload["runtime_events_count"] == 50
    assert record.payload["runtime_events_truncated"] is True

    persisted = tracker.read_persisted_payload("t1")
    assert persisted is not None
    assert len(persisted["runtime_events"]) == 50


def test_mark_terminal_keeps_full_payload_in_memory_without_persistence():
    tracker = RuntimeTaskTracker()
    tracker.configure_compaction(_compact)
    _create_pending(tracker)
    tracker.mark_running("t1")

    tracker.mark_terminal("t1", status="success", payload=_terminal_payload())

    record = tracker.get("t1")
    assert record.payload_compacted is False
    assert len(record.payload["runtime_events"]) == 50
    assert tracker.read_persisted_payload("t1") is None


def test_compacted_record_does_not_overwrite_persisted_full_payload(tmp_path):
    tracker = RuntimeTaskTracker(storage_dir=tmp_path)
    tracker.configure_compaction(_compact)
    _create_pending(tracker)
    tracker.mark_running("t1")
    tracker.mark_terminal("t1", status="success", payload=_terminal_payload())

    tracker.update_observation("t1", progress={"step": 1})

    persisted = tracker.read_persisted_payload("t1")
    assert persisted is not None
    assert len(persisted["runtime_events"]) == 50
    assert "progress" not in persisted


def test_load_persisted_records_compacts_terminal_payloads_in_memory(tmp_path):
    writer = RuntimeTaskTracker(storage_dir=tmp_path)
    _create_pending(writer)
    writer.mark_running("t1")
    writer.mark_terminal("t1", status="success", payload=_terminal_payload())

    reader = RuntimeTaskTracker(storage_dir=tmp_path)
    reader.configure_compaction(_compact)
    loaded = reader.load_persisted_records()

    assert loaded == 1
    record = reader.get("t1")
    assert record.payload_compacted is True
    assert len(record.payload["runtime_events"]) == 10
    assert len(reader.read_persisted_payload("t1")["runtime_events"]) == 50


def test_force_cancel_after_compaction_persists_new_terminal_payload(tmp_path):
    tracker = RuntimeTaskTracker(storage_dir=tmp_path)
    tracker.configure_compaction(_compact)
    _create_pending(tracker)
    tracker.mark_running("t1")
    tracker.mark_terminal("t1", status="blocked", payload=_terminal_payload())
    assert tracker.get("t1").payload_compacted is True

    tracker.cancel("t1", reason="forced", force=True)

    record = tracker.get("t1")
    assert record.status == "cancelled"
    persisted = tracker.read_persisted_payload("t1")
    assert persisted is not None
    assert persisted["status"] == "cancelled"
