from __future__ import annotations

from pathlib import Path

import pytest

from efp_runtime import (
    FileSessionStore,
    InMemorySessionStore,
    MessagePart,
    ToolCall,
    ToolResult,
)
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime


ROOT = Path(__file__).resolve().parents[2]


def test_in_memory_checkpoint_restore_returns_to_checkpoint_history():
    store = InMemorySessionStore()
    session = store.create_session(session_id="session-memory")
    store.append_message(
        session.session_id,
        role="user",
        message_id="msg-1",
        parts=[MessagePart.text_part("before tools")],
    )

    checkpoint = store.create_checkpoint(
        session.session_id,
        checkpoint_id="checkpoint-before-tools",
        label="before tools",
        metadata={"phase": 13},
    )
    store.append_message(
        session.session_id,
        role="assistant",
        message_id="msg-2",
        parts=[MessagePart.text_part("after checkpoint")],
    )

    restored = store.restore_checkpoint(
        session.session_id,
        checkpoint.checkpoint_id,
    )

    assert checkpoint.message_id is None
    assert checkpoint.message_count == 1
    assert checkpoint.label == "before tools"
    assert checkpoint.metadata == {"phase": 13}
    assert [message.message_id for message in restored.messages] == ["msg-1"]
    assert [message.message_id for message in store.read_history(session.session_id)] == [
        "msg-1"
    ]


def test_checkpoint_with_message_id_truncates_snapshot_to_that_message():
    store = InMemorySessionStore()
    session = store.create_session(session_id="session-truncate")
    for index in range(1, 4):
        store.append_message(
            session.session_id,
            role="user",
            message_id=f"msg-{index}",
            parts=[MessagePart.text_part(str(index))],
        )

    checkpoint = store.create_checkpoint(
        session.session_id,
        checkpoint_id="checkpoint-through-msg-2",
        message_id="msg-2",
    )
    store.append_message(
        session.session_id,
        role="assistant",
        message_id="msg-4",
        parts=[MessagePart.text_part("after")],
    )

    restored = store.restore_checkpoint(
        session.session_id,
        checkpoint.checkpoint_id,
    )

    assert checkpoint.message_id == "msg-2"
    assert checkpoint.message_count == 2
    assert [message.message_id for message in restored.messages] == ["msg-1", "msg-2"]

    with pytest.raises(KeyError, match="unknown message"):
        store.create_checkpoint(session.session_id, message_id="missing")


def test_checkpoint_list_and_delete_are_stable():
    store = InMemorySessionStore()
    session = store.create_session(session_id="session-list")
    store.append_message(
        session.session_id,
        role="user",
        message_id="msg-1",
        parts=[MessagePart.text_part("one")],
    )

    first = store.create_checkpoint(
        session.session_id,
        checkpoint_id="checkpoint-a",
    )
    second = store.create_checkpoint(
        session.session_id,
        checkpoint_id="checkpoint-b",
    )

    assert [checkpoint.checkpoint_id for checkpoint in store.list_checkpoints(session.session_id)] == [
        first.checkpoint_id,
        second.checkpoint_id,
    ]
    assert store.delete_checkpoint(session.session_id, first.checkpoint_id) is True
    assert store.delete_checkpoint(session.session_id, first.checkpoint_id) is False
    assert [checkpoint.checkpoint_id for checkpoint in store.list_checkpoints(session.session_id)] == [
        second.checkpoint_id
    ]


def test_file_checkpoint_persists_and_restores_across_store_instances(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    session = store.create_session(session_id="session-file")
    store.append_message(
        session.session_id,
        role="user",
        message_id="msg-1",
        parts=[MessagePart.text_part("persist checkpoint")],
    )
    checkpoint = store.create_checkpoint(
        session.session_id,
        checkpoint_id="checkpoint-disk",
        label="disk",
        metadata={"review": "pending"},
    )
    store.append_message(
        session.session_id,
        role="assistant",
        message_id="msg-2",
        parts=[MessagePart.text_part("remove on restore")],
    )

    checkpoint_file = (
        tmp_path
        / "checkpoints"
        / session.session_id
        / f"{checkpoint.checkpoint_id}.json"
    )
    reloaded = FileSessionStore(tmp_path)
    checkpoints = reloaded.list_checkpoints(session.session_id)
    restored = reloaded.restore_checkpoint(session.session_id, checkpoint.checkpoint_id)

    assert checkpoint_file.is_file()
    assert [item.checkpoint_id for item in checkpoints] == [checkpoint.checkpoint_id]
    assert checkpoints[0].label == "disk"
    assert checkpoints[0].metadata == {"review": "pending"}
    assert [message.message_id for message in restored.messages] == ["msg-1"]
    assert [message.message_id for message in reloaded.get_session(session.session_id).messages] == [
        "msg-1"
    ]
    assert all(message.session_id == session.session_id for message in restored.messages)
    assert all(
        part.session_id == session.session_id
        for message in restored.messages
        for part in message.parts
    )


def test_restore_preserves_tool_call_result_pairing():
    store = InMemorySessionStore()
    session = store.create_session(session_id="session-tools")
    message = store.append_message(
        session.session_id,
        role="assistant",
        message_id="msg-tools",
    )
    call = ToolCall(
        tool_name="read_file",
        arguments={"path": "README.md"},
        call_id="call-read",
    )
    call_part = store.append_part(
        session.session_id,
        message.message_id,
        MessagePart.tool_call_part(call, part_id="part-call"),
    )
    result_part = store.append_part(
        session.session_id,
        message.message_id,
        MessagePart.tool_result_part(
            ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                output="contents",
            ),
            part_id="part-result",
        ),
    )
    checkpoint = store.create_checkpoint(
        session.session_id,
        checkpoint_id="checkpoint-tools",
    )
    store.append_message(
        session.session_id,
        role="user",
        message_id="msg-after-tools",
        parts=[MessagePart.text_part("after")],
    )

    store.restore_checkpoint(session.session_id, checkpoint.checkpoint_id)
    pairs = store.tool_pairs(session.session_id)

    assert sorted(pairs) == ["call-read"]
    assert pairs["call-read"][0].part_id == call_part.part_id
    assert pairs["call-read"][1].part_id == result_part.part_id


def test_agent_runtime_facade_proxies_checkpoint_api():
    store = InMemorySessionStore()
    session = store.create_session(session_id="session-runtime")
    store.append_message(
        session.session_id,
        role="user",
        message_id="msg-1",
        parts=[MessagePart.text_part("checkpoint through runtime")],
    )
    runtime = AgentRuntime(provider=ScriptedLLMProvider([]), store=store)

    checkpoint = runtime.create_checkpoint(
        session.session_id,
        label="runtime",
        metadata={"caller": "facade"},
    )
    store.append_message(
        session.session_id,
        role="assistant",
        message_id="msg-2",
        parts=[MessagePart.text_part("after checkpoint")],
    )
    restored = runtime.restore_checkpoint(session.session_id, checkpoint.checkpoint_id)

    assert [item.checkpoint_id for item in runtime.list_checkpoints(session.session_id)] == [
        checkpoint.checkpoint_id
    ]
    assert [message.message_id for message in restored.messages] == ["msg-1"]
    assert runtime.delete_checkpoint(session.session_id, checkpoint.checkpoint_id) is True
    assert runtime.delete_checkpoint(session.session_id, checkpoint.checkpoint_id) is False


def test_agent_runtime_rejects_store_without_checkpoint_methods():
    runtime = AgentRuntime(provider=ScriptedLLMProvider([]), store=object())

    with pytest.raises(TypeError, match="session store does not support checkpoints"):
        runtime.create_checkpoint("session-missing")


def test_checkpoint_source_stays_inside_runtime_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            ROOT / "src/efp_runtime/session/checkpoint.py",
            ROOT / "src/efp_runtime/session/store.py",
            ROOT / "src/efp_runtime/session/file_store.py",
            ROOT / "src/efp_runtime/runtime/agent.py",
        ]
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.sessions",
        "import src.sessions",
        "from src.agents.core",
        "import src.agents.core",
        "from src.runtime",
        "import src.runtime",
        "from src.skills",
        "import src.skills",
    ]
    for token in forbidden_tokens:
        assert token not in combined
