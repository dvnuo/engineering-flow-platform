from __future__ import annotations

import pytest

from efp_runtime import CompactionPart, Message, MessagePart, MessageRole, Session
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime
from efp_runtime.session import query_messages, query_sessions, session_context_messages


def _session(
    session_id: str,
    updated_at: str,
    *,
    title: str | None = None,
    metadata: dict | None = None,
) -> Session:
    return Session(
        session_id=session_id,
        title=title,
        metadata=dict(metadata or {}),
        created_at=updated_at,
        updated_at=updated_at,
    )


def _message(
    message_id: str,
    created_at: str,
    *,
    text: str | None = None,
    compaction: bool = False,
) -> Message:
    parts = []
    if text is not None:
        parts.append(MessagePart.text_part(text))
    if compaction:
        parts.append(
            MessagePart.compaction_part(
                CompactionPart(summary=f"summary {message_id}")
            )
        )
    return Message(
        role=MessageRole.ASSISTANT,
        session_id="session-1",
        message_id=message_id,
        parts=parts,
        created_at=created_at,
    )


def test_query_sessions_filters_orders_limits_and_copies_results():
    sessions = [
        _session(
            "session-a",
            "2026-05-29T00:00:01Z",
            title="Alpha Root",
            metadata={"path": "/repo", "workspace_id": "workspace-1"},
        ),
        _session(
            "session-b",
            "2026-05-29T00:00:02Z",
            title="Beta Child",
            metadata={
                "parent_session_id": "session-a",
                "path": "/repo/service",
                "workspace_id": "workspace-1",
            },
        ),
        _session(
            "session-c",
            "2026-05-29T00:00:03Z",
            title="Gamma Root",
            metadata={
                "parent_session_id": "",
                "path": "/other",
                "workspace_id": "workspace-2",
            },
        ),
        _session(
            "session-d",
            "2026-05-29T00:00:04Z",
            metadata={"path": "/repo-archive", "workspace_id": "workspace-1"},
        ),
    ]

    assert [session.session_id for session in query_sessions(sessions)] == [
        "session-d",
        "session-c",
        "session-b",
        "session-a",
    ]
    assert [
        session.session_id for session in query_sessions(sessions, order="asc", limit=2)
    ] == ["session-a", "session-b"]
    assert [
        session.session_id for session in query_sessions(sessions, search="root")
    ] == ["session-c", "session-a"]
    assert [session.session_id for session in query_sessions(sessions, roots=True)] == [
        "session-d",
        "session-c",
        "session-a",
    ]
    assert [
        session.session_id
        for session in query_sessions(sessions, parent_session_id="session-a")
    ] == ["session-b"]
    assert [session.session_id for session in query_sessions(sessions, path="/repo")] == [
        "session-b",
        "session-a",
    ]
    assert [
        session.session_id
        for session in query_sessions(sessions, workspace_id="workspace-2")
    ] == ["session-c"]
    assert [
        session.session_id
        for session in query_sessions(sessions, start="2026-05-29T00:00:03Z")
    ] == ["session-d", "session-c"]

    result = query_sessions(sessions, search="alpha")
    result[0].title = "mutated"
    assert sessions[0].title == "Alpha Root"


def test_query_sessions_cursor_next_and_previous():
    sessions = [
        _session("session-a", "2026-05-29T00:00:01Z"),
        _session("session-b", "2026-05-29T00:00:02Z"),
        _session("session-c", "2026-05-29T00:00:03Z"),
        _session("session-d", "2026-05-29T00:00:04Z"),
    ]

    assert [
        session.session_id
        for session in query_sessions(
            sessions,
            limit=2,
            cursor={"id": "session-c"},
        )
    ] == ["session-b", "session-a"]
    assert [
        session.session_id
        for session in query_sessions(
            sessions,
            limit=2,
            cursor={"session_id": "session-b", "direction": "previous"},
        )
    ] == ["session-d", "session-c"]
    assert [
        session.session_id
        for session in query_sessions(
            sessions,
            order="asc",
            limit=2,
            cursor={"session_id": "session-b", "time": "2026-05-29T00:00:02Z"},
        )
    ] == ["session-c", "session-d"]


def test_query_messages_orders_limits_cursors_validation_and_copies_results():
    messages = [
        _message("msg-1", "2026-05-29T00:00:01Z", text="one"),
        _message("msg-2", "2026-05-29T00:00:02Z", text="two"),
        _message("msg-3", "2026-05-29T00:00:03Z", text="three"),
        _message("msg-4", "2026-05-29T00:00:04Z", text="four"),
    ]

    assert [message.message_id for message in query_messages(messages)] == [
        "msg-1",
        "msg-2",
        "msg-3",
        "msg-4",
    ]
    assert [
        message.message_id for message in query_messages(messages, order="desc", limit=2)
    ] == ["msg-4", "msg-3"]
    assert [
        message.message_id
        for message in query_messages(
            messages,
            limit=2,
            cursor={"message_id": "msg-2"},
        )
    ] == ["msg-3", "msg-4"]
    assert [
        message.message_id
        for message in query_messages(
            messages,
            limit=2,
            cursor={"id": "msg-3", "direction": "previous"},
        )
    ] == ["msg-1", "msg-2"]
    assert [
        message.message_id
        for message in query_messages(
            messages,
            order="desc",
            limit=2,
            cursor={"message_id": "msg-3"},
        )
    ] == ["msg-2", "msg-1"]
    assert [
        message.message_id
        for message in query_messages(
            messages,
            order="desc",
            limit=1,
            cursor={"message_id": "msg-2", "direction": "previous"},
        )
    ] == ["msg-3"]
    assert query_messages(messages, limit=0) == []

    result = query_messages(messages, limit=1)
    result[0].parts[0].text = "mutated"
    assert messages[0].parts[0].text == "one"

    with pytest.raises(ValueError, match="order"):
        query_messages(messages, order="newest")
    with pytest.raises(ValueError, match="limit"):
        query_messages(messages, limit=-1)
    with pytest.raises(ValueError, match="direction"):
        query_messages(messages, cursor={"message_id": "msg-1", "direction": "back"})
    with pytest.raises(ValueError, match="cursor must include"):
        query_messages(messages, cursor={"time": "2026-05-29T00:00:01Z"})
    with pytest.raises(ValueError, match="cursor object not found"):
        query_messages(messages, cursor={"message_id": "missing"})


def test_session_context_messages_starts_at_latest_compaction():
    messages = [
        _message("msg-1", "2026-05-29T00:00:01Z", text="one"),
        _message("msg-2", "2026-05-29T00:00:02Z", text="two", compaction=True),
        _message("msg-3", "2026-05-29T00:00:03Z", text="three"),
        _message("msg-4", "2026-05-29T00:00:04Z", text="four", compaction=True),
        _message("msg-5", "2026-05-29T00:00:05Z", text="five"),
    ]

    assert [
        message.message_id for message in session_context_messages(messages[:1] + messages[2:3])
    ] == ["msg-1", "msg-3"]
    assert [message.message_id for message in session_context_messages(reversed(messages))] == [
        "msg-4",
        "msg-5",
    ]


def test_agent_runtime_query_views_work_through_facade():
    runtime = AgentRuntime(provider=ScriptedLLMProvider([]))
    runtime.create_session(
        session_id="session-source",
        title="Source Session",
        metadata={"path": "/repo", "workspace_id": "workspace-1"},
    )
    runtime.create_session(
        session_id="session-child",
        title="Child Session",
        metadata={
            "parent_session_id": "session-source",
            "path": "/repo/service",
            "workspace_id": "workspace-1",
        },
    )
    runtime.store.replace_history(
        "session-source",
        [
            _message("msg-1", "2026-05-29T00:00:01Z", text="one"),
            _message("msg-2", "2026-05-29T00:00:02Z", text="two", compaction=True),
            _message("msg-3", "2026-05-29T00:00:03Z", text="three"),
            _message("msg-4", "2026-05-29T00:00:04Z", text="four"),
        ],
    )

    assert [
        session.session_id for session in runtime.query_sessions(search="source")
    ] == ["session-source"]
    assert [
        session.session_id for session in runtime.query_sessions(path="/repo", roots=True)
    ] == ["session-source"]
    assert [
        session.session_id
        for session in runtime.query_sessions(parent_session_id="session-source")
    ] == ["session-child"]
    assert [
        message.message_id
        for message in runtime.session_messages(
            "session-source",
            limit=2,
            cursor={"message_id": "msg-1"},
        )
    ] == ["msg-2", "msg-3"]
    assert [
        message.message_id
        for message in runtime.session_messages("session-source", order="desc", limit=2)
    ] == ["msg-4", "msg-3"]
    assert [message.message_id for message in runtime.session_context("session-source")] == [
        "msg-2",
        "msg-3",
        "msg-4",
    ]
