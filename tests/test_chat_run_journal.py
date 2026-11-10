from src.gateway.chat_run_journal import ChatRunJournal


def _text_delta(text, *, part_id="part-1", message_id="msg-1", event_id=""):
    # The projected event repeats its text under every spelling a viewer may read.
    data = {
        "message_id": message_id,
        "part_id": part_id,
        "delta": text,
        "text_delta": text,
        "content_delta": text,
        "message_delta": text,
        "content": text,
        "text": text,
    }
    return {
        "id": event_id,
        "type": "session.next.text.delta",
        "event_type": "session.next.text.delta",
        "session_id": "s-1",
        "request_id": "req-1",
        "message_id": message_id,
        "part_id": part_id,
        "summary": text,
        "properties": dict(data),
        "data": dict(data),
    }


def _tool_input_delta(text, *, call_id, message_id="msg-1"):
    data = {
        "tool_call_id": call_id,
        "tool": "bash",
        "message_id": message_id,
        "delta": text,
        "input_delta": text,
        "arguments_delta": text,
        "input_preview": text,
        "arguments_preview": text,
    }
    return {
        "type": "session.next.tool.input.delta",
        "event_type": "session.next.tool.input.delta",
        "session_id": "s-1",
        "request_id": "req-1",
        "message_id": message_id,
        "properties": dict(data),
        "data": dict(data),
    }


def _step(name, *, padding=""):
    data = {"step": name, "padding": padding}
    return {
        "id": f"evt-{name}",
        "type": "session.next.step.started",
        "event_type": "session.next.step.started",
        "session_id": "s-1",
        "request_id": "req-1",
        "properties": dict(data),
        "data": dict(data),
    }


def _served(journal, after_seq=0):
    return [event for event, _ts in journal.records_after(after_seq)]


def test_chat_run_journal_starts_empty():
    journal = ChatRunJournal()

    assert journal.last_seq == 0
    assert journal.first_seq == 1
    assert journal.truncated is False
    assert journal.can_serve_after(0) is True
    assert journal.records_after(0) == []


def test_chat_run_journal_serves_everything_after_a_sequence_number_in_order():
    journal = ChatRunJournal()
    for seq, name in enumerate(("one", "two", "three"), start=1):
        journal.append(seq, _step(name))

    served = journal.records_after(1)

    assert [event["id"] for event, _ts in served] == ["evt-two", "evt-three"]
    assert [event["run_seq"] for event, _ts in served] == [2, 3]
    assert all("run_seq_from" not in event for event, _ts in served)
    assert all(isinstance(ts, float) for _event, ts in served)
    assert journal.records_after(3) == []
    assert journal.last_seq == 3
    assert journal.first_seq == 1


def test_chat_run_journal_keeps_consecutive_deltas_of_one_part_as_one_record():
    journal = ChatRunJournal()
    journal.append(1, _text_delta("Hel", event_id="e1"))
    journal.append(2, _text_delta("lo ", event_id="e2"))
    journal.append(3, _text_delta("world", event_id="e3"))

    served = _served(journal)

    assert len(served) == 1
    event = served[0]
    # The newest delta stands for the stretch, and says where the stretch starts.
    assert event["id"] == "e3@1-3"
    assert event["run_seq"] == 3
    assert event["run_seq_from"] == 1
    for payload in (event["data"], event["properties"]):
        assert payload["delta"] == "Hello world"
        assert payload["part_id"] == "part-1"
        assert payload["message_id"] == "msg-1"
        # The joined text is sent once, not under every spelling.
        for alias in ("text_delta", "content_delta", "message_delta", "content", "text"):
            assert alias not in payload


def test_chat_run_journal_serves_a_viewer_part_way_through_only_the_rest():
    journal = ChatRunJournal()
    journal.append(1, _text_delta("Hel", event_id="e1"))
    journal.append(2, _text_delta("lo ", event_id="e2"))
    journal.append(3, _text_delta("world", event_id="e3"))

    after_first = _served(journal, 1)
    assert len(after_first) == 1
    assert after_first[0]["run_seq"] == 3
    assert after_first[0]["run_seq_from"] == 2
    assert after_first[0]["data"]["delta"] == "lo world"
    assert after_first[0]["properties"]["delta"] == "lo world"

    # Only the newest delta is missing: it is served as it was sent.
    after_second = _served(journal, 2)
    assert len(after_second) == 1
    assert after_second[0]["run_seq"] == 3
    assert "run_seq_from" not in after_second[0]
    assert after_second[0]["data"] == _text_delta("world")["data"]
    assert after_second[0]["properties"] == _text_delta("world")["properties"]

    assert _served(journal, 3) == []


def test_chat_run_journal_gives_a_stretch_of_deltas_an_id_of_its_own():
    journal = ChatRunJournal()
    # Ids are content hashes: the same words twice in one part share one.
    journal.append(1, _text_delta(" the", event_id="hash-the"))
    journal.append(2, _text_delta(" cat and", event_id="hash-cat"))
    journal.append(3, _text_delta(" the", event_id="hash-the"))

    # A viewer that has the first delta has met "hash-the", and would skip the
    # rest of the text if the stretch came under that id.
    stretch = _served(journal, 1)[0]
    assert stretch["data"]["delta"] == " cat and the"
    assert stretch["id"] == "hash-the@2-3"
    assert _served(journal, 0)[0]["id"] == "hash-the@1-3"
    # One delta is the event that was sent, id and all.
    assert _served(journal, 2)[0]["id"] == "hash-the"
    # An event without an id is not given one.
    unnamed = ChatRunJournal()
    unnamed.append(1, _text_delta("a"))
    unnamed.append(2, _text_delta("b"))
    assert _served(unnamed)[0]["id"] == ""


def test_chat_run_journal_serves_the_same_thing_twice():
    journal = ChatRunJournal()
    journal.append(1, _text_delta("Hel", event_id="e1"))
    journal.append(2, _text_delta("lo ", event_id="e2"))
    journal.append(3, _text_delta("world", event_id="e3"))

    assert journal.records_after(0) == journal.records_after(0)
    assert journal.records_after(1) == journal.records_after(1)
    # Serving the joined text did not rewrite what the journal holds.
    assert _served(journal, 2)[0]["data"]["text"] == "world"


def test_chat_run_journal_does_not_merge_deltas_of_different_parts():
    journal = ChatRunJournal()
    journal.append(1, _text_delta("first part", part_id="part-1"))
    journal.append(2, _text_delta("second part", part_id="part-2"))

    served = _served(journal)

    assert [event["run_seq"] for event in served] == [1, 2]
    assert [event["data"]["delta"] for event in served] == ["first part", "second part"]
    assert all("run_seq_from" not in event for event in served)


def test_chat_run_journal_does_not_merge_the_arguments_of_two_tool_calls():
    journal = ChatRunJournal()
    # Two calls of one message streaming their arguments in turn.
    journal.append(1, _tool_input_delta('{"command": ', call_id="call-1"))
    journal.append(2, _tool_input_delta('"ls"}', call_id="call-1"))
    journal.append(3, _tool_input_delta('{"command": ', call_id="call-2"))
    journal.append(4, _tool_input_delta('"pwd"}', call_id="call-2"))

    served = _served(journal)

    assert [event["run_seq"] for event in served] == [2, 4]
    assert [event["run_seq_from"] for event in served] == [1, 3]
    assert [event["data"]["tool_call_id"] for event in served] == ["call-1", "call-2"]
    assert [event["data"]["delta"] for event in served] == ['{"command": "ls"}', '{"command": "pwd"}']
    for event in served:
        assert "input_delta" not in event["data"]
        assert "arguments_delta" not in event["data"]


def test_chat_run_journal_starts_a_new_record_after_any_other_event():
    journal = ChatRunJournal()
    journal.append(1, _text_delta("before "))
    journal.append(2, _text_delta("the tool. "))
    journal.append(3, _step("tool"))
    journal.append(4, _text_delta("after it."))

    served = _served(journal)

    assert [event["run_seq"] for event in served] == [2, 3, 4]
    assert served[0]["run_seq_from"] == 1
    assert served[0]["data"]["delta"] == "before the tool. "
    assert "run_seq_from" not in served[2]
    assert served[2]["data"]["delta"] == "after it."


def test_chat_run_journal_does_not_merge_a_delta_event_without_text():
    journal = ChatRunJournal()
    journal.append(1, _text_delta("text"))
    journal.append(2, _text_delta(""))
    journal.append(3, _text_delta("more"))

    assert [event["run_seq"] for event in _served(journal)] == [1, 2, 3]


def test_chat_run_journal_splits_a_long_answer_into_records_under_the_cap():
    cap = 2048
    journal = ChatRunJournal(max_merged_record_bytes=cap)
    pieces = [f"[{index:03d}] part of the final answer. " for index in range(1, 201)]
    for seq, piece in enumerate(pieces, start=1):
        journal.append(seq, _text_delta(piece))

    served = _served(journal)

    assert 1 < len(served) < len(pieces)
    # Nothing is lost and nothing overlaps: each record follows on from the last.
    assert "".join(event["data"]["delta"] for event in served) == "".join(pieces)
    next_seq = 1
    for event in served:
        assert event.get("run_seq_from", event["run_seq"]) == next_seq
        assert len(event["data"]["delta"]) <= cap
        next_seq = event["run_seq"] + 1
    assert next_seq == len(pieces) + 1

    # A viewer in the middle of a record gets the rest of it, then the others.
    middle = served[0]["run_seq"] // 2
    resumed = _served(journal, middle)
    assert resumed[0]["run_seq_from"] == middle + 1
    assert "".join(event["data"]["delta"] for event in resumed) == "".join(pieces[middle:])


def test_chat_run_journal_drops_the_oldest_records_at_its_record_cap():
    journal = ChatRunJournal(max_records=3)
    for seq in range(1, 6):
        journal.append(seq, _step(f"step-{seq}"))

    assert journal.first_seq == 3
    assert journal.last_seq == 5
    assert journal.truncated is True
    assert journal.can_serve_after(2) is True
    assert journal.can_serve_after(1) is False
    assert [event["run_seq"] for event in _served(journal)] == [3, 4, 5]


def test_chat_run_journal_drops_the_oldest_records_at_its_byte_cap():
    journal = ChatRunJournal(max_bytes=1500)
    for seq in range(1, 6):
        journal.append(seq, _step(f"step-{seq}", padding="x" * 200))

    assert journal.truncated is True
    assert journal.last_seq == 5
    kept = [event["run_seq"] for event in _served(journal)]
    assert 0 < len(kept) < 5
    assert kept[-1] == 5
    assert kept[0] == journal.first_seq


def test_chat_run_journal_always_keeps_the_event_just_written():
    journal = ChatRunJournal(max_bytes=10)
    journal.append(1, _step("one", padding="x" * 200))
    journal.append(2, _step("two", padding="x" * 200))

    assert [event["id"] for event in _served(journal)] == ["evt-two"]
    assert journal.first_seq == 2


def test_chat_run_journal_ignores_a_sequence_number_it_already_has():
    journal = ChatRunJournal()
    journal.append(1, _step("one"))
    journal.append(2, _step("two"))
    journal.append(2, _step("two-again"))
    journal.append(1, _step("one-again"))

    assert [event["id"] for event in _served(journal)] == ["evt-one", "evt-two"]
    assert journal.last_seq == 2
