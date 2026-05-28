import pytest

from efp_runtime import (
    CompactionPart,
    InMemorySessionStore,
    MessagePart,
    MessagePartType,
    TaskPart,
    ToolCall,
    ToolResult,
)


def test_store_creates_sessions_appends_messages_and_reads_history():
    store = InMemorySessionStore()
    session = store.create_session(session_id="session-1", title="Runtime v2")

    user_message = store.append_message(
        session.session_id,
        role="user",
        parts=[MessagePart.text_part("Build the runtime v2 foundation.")],
    )
    assistant_message = store.append_message(session.session_id, role="assistant")
    store.append_part(
        session.session_id,
        assistant_message.message_id,
        MessagePart.reasoning_part("Create contracts before processors."),
    )
    store.append_part(
        session.session_id,
        assistant_message.message_id,
        MessagePart.compaction_part(
            CompactionPart(
                summary="Runtime v2 scope is contracts and store only.",
                source_message_ids=[user_message.message_id],
                auto=False,
            )
        ),
    )
    store.append_part(
        session.session_id,
        assistant_message.message_id,
        MessagePart.task_part(TaskPart(prompt="Add tests", status="completed")),
    )

    history = store.read_history(session.session_id)

    assert [message.role.value for message in history] == ["user", "assistant"]
    assert history[0].parts[0].type is MessagePartType.TEXT
    assert history[1].parts[0].type is MessagePartType.REASONING
    assert history[1].parts[1].type is MessagePartType.COMPACTION
    assert history[1].parts[2].type is MessagePartType.TASK


def test_store_lists_deletes_and_removes_checkpoints():
    store = InMemorySessionStore()
    store.create_session(session_id="session-b")
    store.create_session(session_id="session-a", title="A")
    store.create_checkpoint("session-a", checkpoint_id="checkpoint-a")

    listed = store.list_sessions()
    assert [session.session_id for session in listed] == [
        "session-a",
        "session-b",
    ]
    listed[0].title = "mutated"
    assert store.get_session("session-a").title == "A"
    assert store.delete_session("session-a") is True
    assert store.delete_session("session-a") is False
    assert [session.session_id for session in store.list_sessions()] == ["session-b"]
    assert "session-a" not in store._checkpoints


def test_store_updates_session_metadata_title_and_updated_at_with_deepcopy():
    store = InMemorySessionStore()
    created = store.create_session(
        session_id="session-update",
        title="Original",
        metadata={"keep": True, "nested": {"value": 1}},
    )

    merged = store.update_session(
        "session-update",
        title="Updated",
        metadata={"added": {"value": 2}},
    )

    assert merged.title == "Updated"
    assert merged.metadata == {
        "keep": True,
        "nested": {"value": 1},
        "added": {"value": 2},
    }
    assert merged.updated_at != created.updated_at
    merged.metadata["added"]["value"] = 99
    assert store.get_session("session-update").metadata["added"] == {"value": 2}

    title_only = store.update_session("session-update", title="Title only")
    assert title_only.title == "Title only"
    assert title_only.metadata == {
        "keep": True,
        "nested": {"value": 1},
        "added": {"value": 2},
    }

    replaced = store.update_session(
        "session-update",
        metadata={"replacement": {"value": 3}},
        replace_metadata=True,
    )

    assert replaced.title == "Title only"
    assert replaced.metadata == {"replacement": {"value": 3}}
    replaced.metadata["replacement"]["value"] = 100
    assert store.get_session("session-update").metadata == {
        "replacement": {"value": 3}
    }


def test_store_forks_session_through_message_and_rebinds_history():
    store = InMemorySessionStore()
    session = store.create_session(
        session_id="session-source",
        title="Source",
        metadata={"suite": "memory"},
    )
    store.append_message(
        session.session_id,
        role="user",
        message_id="msg-1",
        parts=[MessagePart.text_part("one", part_id="part-1")],
    )
    store.append_message(
        session.session_id,
        role="assistant",
        message_id="msg-2",
        parts=[MessagePart.text_part("two", part_id="part-2")],
    )
    store.append_message(
        session.session_id,
        role="user",
        message_id="msg-3",
        parts=[MessagePart.text_part("three", part_id="part-3")],
    )

    fork = store.fork_session(
        session.session_id,
        message_id="msg-2",
        new_session_id="session-fork",
    )

    assert fork.title == "Source"
    assert [message.message_id for message in fork.messages] == ["msg-1", "msg-2"]
    assert fork.metadata == {
        "suite": "memory",
        "parent_session_id": "session-source",
        "forked_from_message_id": "msg-2",
    }
    assert all(message.session_id == "session-fork" for message in fork.messages)
    assert all(
        part.session_id == "session-fork" and part.message_id == message.message_id
        for message in fork.messages
        for part in message.parts
    )
    assert [message.message_id for message in store.read_history(session.session_id)] == [
        "msg-1",
        "msg-2",
        "msg-3",
    ]
    with pytest.raises(ValueError, match="session already exists"):
        store.fork_session(session.session_id, new_session_id="session-fork")


def test_store_preserves_tool_call_result_pairing():
    store = InMemorySessionStore()
    session = store.create_session(session_id="session-tools")
    assistant_message = store.append_message(session.session_id, role="assistant")

    call = ToolCall(tool_name="read_file", arguments={"path": "README.md"}, call_id="call-read")
    call_part = store.append_part(
        session.session_id,
        assistant_message.message_id,
        MessagePart.tool_call_part(call),
    )
    result_part = store.append_part(
        session.session_id,
        assistant_message.message_id,
        MessagePart.tool_result_part(
            ToolResult(call_id=call.call_id, tool_name=call.tool_name, output="README contents")
        ),
    )

    history = store.read_history(session.session_id)
    pairs = store.tool_pairs(session.session_id)

    assert history[0].parts[0].tool_call.call_id == history[0].parts[1].tool_result.call_id
    assert pairs["call-read"][0].part_id == call_part.part_id
    assert pairs["call-read"][1].part_id == result_part.part_id


def test_store_rejects_unpaired_or_mismatched_tool_results():
    store = InMemorySessionStore()
    session = store.create_session(session_id="session-invalid")
    assistant_message = store.append_message(session.session_id, role="assistant")

    with pytest.raises(ValueError, match="no matching tool call"):
        store.append_part(
            session.session_id,
            assistant_message.message_id,
            MessagePart.tool_result_part(
                ToolResult(call_id="missing-call", tool_name="read_file", output="")
            ),
        )

    call = ToolCall(tool_name="read_file", arguments={}, call_id="call-mismatch")
    store.append_part(session.session_id, assistant_message.message_id, MessagePart.tool_call_part(call))

    with pytest.raises(ValueError, match="tool name mismatch"):
        store.append_part(
            session.session_id,
            assistant_message.message_id,
            MessagePart.tool_result_part(
                ToolResult(call_id=call.call_id, tool_name="write_file", output="")
            ),
        )
