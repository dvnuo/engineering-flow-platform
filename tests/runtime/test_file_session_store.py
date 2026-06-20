from __future__ import annotations

from pathlib import Path

import pytest

from efp_runtime import (
    Attachment,
    CompactionPart,
    FileSessionStore,
    MessagePart,
    MessagePartType,
    MessageRole,
    RuntimeEvent,
    SkillPackage,
    TaskPart,
    ToolCall,
    ToolResult,
)
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime


ROOT = Path(__file__).resolve().parents[2]


def test_file_store_persists_sessions_across_instances(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    session = store.create_session(
        session_id="session-persist",
        title="Persistent EFP runtime",
        metadata={"suite": "file"},
    )
    store.append_message(
        session.session_id,
        role="user",
        parts=[MessagePart.text_part("Persist this session.")],
        message_id="msg-user",
        status="complete",
    )
    store.append_message(
        session.session_id,
        role="assistant",
        parts=[MessagePart.text_part("Persisted.")],
        message_id="msg-assistant",
        status="complete",
    )

    reloaded = FileSessionStore(tmp_path)
    restored = reloaded.get_session(session.session_id)
    history = reloaded.read_history(session.session_id)

    assert restored.title == "Persistent EFP runtime"
    assert restored.metadata == {"suite": "file"}
    assert [message.message_id for message in history] == ["msg-user", "msg-assistant"]
    assert [message.role for message in history] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert history[0].parts[0].text == "Persist this session."
    assert history[1].parts[0].text == "Persisted."


def test_file_store_persists_update_session_across_instances(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    created = store.create_session(
        session_id="session-update",
        title="Original",
        metadata={"keep": True, "nested": {"value": 1}},
    )

    updated = store.update_session(
        created.session_id,
        title="Updated",
        metadata={"nested": {"value": 2}, "added": True},
    )
    assert updated.updated_at != created.updated_at
    replaced = store.update_session(
        created.session_id,
        metadata={"replacement": {"value": 3}},
        replace_metadata=True,
    )
    replaced.metadata["replacement"]["value"] = 99

    restored = FileSessionStore(tmp_path).get_session(created.session_id)

    assert restored.title == "Updated"
    assert restored.metadata == {"replacement": {"value": 3}}


def test_file_store_round_trips_all_current_part_types(tmp_path: Path):
    skill_file = tmp_path / "skills" / "requirements" / "SKILL.md"
    skill = SkillPackage(
        name="requirements",
        content="# Requirements",
        description="Requirements workflow",
        root=skill_file.parent,
        skill_file=skill_file,
        sidecar_files=[skill_file.parent / "notes.md"],
        metadata={"version": 1},
        loaded_at="2026-05-28T00:00:00Z",
    )
    attachment = Attachment(
        attachment_id="att-1",
        mime_type="text/plain",
        filename="notes.txt",
        text_ref="blob:notes",
        metadata={"skill": skill},
        created_at="2026-05-28T00:00:01Z",
    )
    call = ToolCall(
        tool_name="read_file",
        arguments={"path": "README.md"},
        call_id="call-read",
        status="complete",
        arguments_text='{"path": "README.md"}',
        raw={"path_object": skill_file},
        metadata={"skill": skill},
        created_at="2026-05-28T00:00:02Z",
    )
    result = ToolResult(
        call_id=call.call_id,
        tool_name=call.tool_name,
        output={"contents": "README", "skill": skill},
        success=True,
        content="README",
        status="success",
        attachments=[attachment],
        metadata={"skill": skill},
        truncated=True,
        events=[
            RuntimeEvent(
                type="tool.completed",
                session_id="session-parts",
                payload={"tool": "read_file", "skill": skill},
                created_at="2026-05-28T00:00:03Z",
            )
        ],
        created_at="2026-05-28T00:00:04Z",
    )
    compaction = CompactionPart(
        summary="Earlier history was compacted.",
        source_message_ids=["msg-old"],
        auto=True,
        overflow=True,
        tail_start_message_id="msg-tail",
        original_part_count=9,
        original_message_count=4,
        tool_pair_count=1,
        metadata={"skill": skill},
    )
    task = TaskPart(
        prompt="Audit persistence",
        task_id="task-1",
        description="Check file-backed store",
        status="completed",
        agent="runtime",
        model="test-model",
        metadata={"skill": skill},
    )

    store = FileSessionStore(tmp_path)
    session = store.create_session(session_id="session-parts", metadata={"skill": skill})
    store.append_message(
        session.session_id,
        role="assistant",
        message_id="msg-parts",
        parts=[
            MessagePart.text_part("hello", part_id="part-text"),
            MessagePart.reasoning_part("thinking", part_id="part-reasoning"),
            MessagePart.tool_call_part(call, part_id="part-call"),
            MessagePart.tool_result_part(result, part_id="part-result"),
            MessagePart.compaction_part(compaction, part_id="part-compaction"),
            MessagePart.task_part(task, part_id="part-task"),
            MessagePart.attachment_part(attachment, part_id="part-attachment"),
            MessagePart.error_part("failed safely", part_id="part-error"),
        ],
        status="complete",
    )

    restored = FileSessionStore(tmp_path).get_session(session.session_id)
    parts = restored.messages[0].parts

    assert [part.type for part in parts] == [
        MessagePartType.TEXT,
        MessagePartType.REASONING,
        MessagePartType.TOOL_CALL,
        MessagePartType.TOOL_RESULT,
        MessagePartType.COMPACTION,
        MessagePartType.TASK,
        MessagePartType.ATTACHMENT,
        MessagePartType.ERROR,
    ]
    assert isinstance(restored.metadata["skill"], SkillPackage)
    assert parts[0].text == "hello"
    assert parts[1].reasoning == "thinking"
    assert parts[2].tool_call.call_id == "call-read"
    assert isinstance(parts[2].tool_call.raw["path_object"], Path)
    assert isinstance(parts[2].tool_call.metadata["skill"], SkillPackage)
    assert parts[3].tool_result.content == "README"
    assert parts[3].tool_result.truncated is True
    assert isinstance(parts[3].tool_result.output["skill"], SkillPackage)
    assert isinstance(parts[3].tool_result.attachments[0], Attachment)
    assert isinstance(parts[3].tool_result.events[0], RuntimeEvent)
    assert isinstance(parts[3].tool_result.events[0].payload["skill"], SkillPackage)
    assert parts[4].compaction.original_part_count == 9
    assert isinstance(parts[4].compaction.metadata["skill"], SkillPackage)
    assert parts[5].task.agent == "runtime"
    assert isinstance(parts[5].task.metadata["skill"], SkillPackage)
    assert parts[6].attachment.filename == "notes.txt"
    assert isinstance(parts[6].attachment.metadata["skill"], SkillPackage)
    assert parts[7].text == "failed safely"


def test_file_store_tool_pairs(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    session = store.create_session(session_id="session-tools")
    message = store.append_message(session.session_id, role="assistant", message_id="msg-tools")
    call = ToolCall(tool_name="read_file", arguments={"path": "README.md"}, call_id="call-read")
    call_part = store.append_part(
        session.session_id,
        message.message_id,
        MessagePart.tool_call_part(call, part_id="part-call"),
    )
    result_part = store.append_part(
        session.session_id,
        message.message_id,
        MessagePart.tool_result_part(
            ToolResult(call_id=call.call_id, tool_name=call.tool_name, output="contents"),
            part_id="part-result",
        ),
    )

    pairs = FileSessionStore(tmp_path).tool_pairs(session.session_id)

    assert pairs["call-read"][0].part_id == call_part.part_id
    assert pairs["call-read"][1].part_id == result_part.part_id


def test_file_store_todo_store_persists_outside_session_messages(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    store.create_session(session_id="session-todos")
    todos = [
        {"content": "Persist separately", "status": "pending", "priority": "medium"}
    ]

    store.todo_store().set("session-todos", todos)

    reloaded = FileSessionStore(tmp_path)
    assert reloaded.todo_store().get("session-todos") == todos
    assert reloaded.read_history("session-todos") == []
    assert (tmp_path / "todos" / "session-todos.json").exists()

    reloaded.todo_store().clear("session-todos")
    assert reloaded.todo_store().get("session-todos") == []
    assert not (tmp_path / "todos" / "session-todos.json").exists()


def test_file_store_delete_session_clears_todo_file(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    store.create_session(session_id="session-delete-todos")
    todos = [
        {"content": "Remove with session", "status": "pending", "priority": "high"}
    ]
    store.todo_store().set("session-delete-todos", todos)
    todo_path = tmp_path / "todos" / "session-delete-todos.json"

    assert todo_path.exists()
    assert store.delete_session("session-delete-todos") is True

    assert not todo_path.exists()
    assert store.todo_store().get("session-delete-todos") == []


def test_file_store_forks_session_through_message(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    session = store.create_session(
        session_id="session-source",
        title="Source",
        metadata={
            "suite": "file",
            "revert": {"active": False},
            "summary": {"diff_count": 1},
            "last_execution_id": "run-1",
            "last_runtime_status": "completed",
            "last_runtime_updated_at": "2026-05-29T00:00:00Z",
            "pending_permission_request": {"id": "permission-1"},
            "pending_question_request": {"id": "question-1"},
            "pending_tool_calls": [{"call_id": "call-1"}],
        },
    )
    store.append_message(
        session.session_id,
        role="user",
        message_id="msg-1",
        parts=[MessagePart.text_part("one")],
    )
    store.append_message(
        session.session_id,
        role="assistant",
        message_id="msg-2",
        parts=[MessagePart.text_part("two")],
    )
    store.append_message(
        session.session_id,
        role="user",
        message_id="msg-3",
        parts=[MessagePart.text_part("three")],
    )

    fork = store.fork_session(
        session.session_id,
        message_id="msg-2",
        new_session_id="session-fork",
    )

    assert [message.message_id for message in fork.messages] == ["msg-1", "msg-2"]
    assert fork.title == "Source"
    assert fork.metadata == {
        "suite": "file",
        "parent_session_id": "session-source",
        "forked_from_message_id": "msg-2",
    }
    assert [message.message_id for message in store.read_history(session.session_id)] == [
        "msg-1",
        "msg-2",
        "msg-3",
    ]
    reloaded_fork = FileSessionStore(tmp_path).get_session("session-fork")
    assert [message.message_id for message in reloaded_fork.messages] == ["msg-1", "msg-2"]
    assert reloaded_fork.metadata == fork.metadata
    assert all(message.session_id == "session-fork" for message in reloaded_fork.messages)
    assert all(
        part.session_id == "session-fork"
        for message in reloaded_fork.messages
        for part in message.parts
    )


def test_file_store_lists_and_deletes_sessions(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    store.create_session(session_id="session-b")
    store.create_session(session_id="session-a")

    assert [session.session_id for session in store.list_sessions()] == ["session-a", "session-b"]
    assert store.delete_session("session-a") is True
    assert store.delete_session("session-a") is False
    assert [session.session_id for session in store.list_sessions()] == ["session-b"]


def test_file_store_rejects_session_id_path_traversal(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    bad_ids = ["../outside", "nested/session", "/tmp/session", "..\\outside", ".hidden"]

    for session_id in bad_ids:
        with pytest.raises(ValueError):
            store.create_session(session_id=session_id)
        with pytest.raises(ValueError):
            store.get_session(session_id)
        with pytest.raises(ValueError):
            store.delete_session(session_id)


@pytest.mark.asyncio
async def test_agent_runtime_with_file_store_persists_text_only_run(tmp_path: Path):
    store = FileSessionStore(tmp_path / "store")
    provider = ScriptedLLMProvider([{"content": "Persisted through facade."}])
    runtime = AgentRuntime(
        provider=provider,
        workspace_root=tmp_path,
        max_iterations=2,
        store=store,
    )

    result = await runtime.run("Persist via runtime.", session_id="session-agent")

    assert result.status == LoopStatus.COMPLETED
    history = FileSessionStore(tmp_path / "store").read_history(result.session_id)
    assert [message.role for message in history] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert history[0].parts[0].text == "Persist via runtime."
    assert history[1].parts[0].text == "Persisted through facade."


def test_file_session_store_source_stays_inside_runtime_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            ROOT / "src/efp_runtime/session/file_store.py",
            ROOT / "src/efp_runtime/session/protocol.py",
            ROOT / "src/efp_runtime/session/serialization.py",
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
    ]
    for token in forbidden_tokens:
        assert token not in combined
