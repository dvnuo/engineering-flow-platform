from __future__ import annotations

import json
from pathlib import Path

import pytest

from efp_runtime import InMemorySessionStore, Message, MessagePart, MessageRole
from efp_runtime.compaction import prune_old_tool_outputs
from efp_runtime.config_loader import load_runtime_config
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.types import ToolCall, ToolResult


def test_prunes_old_completed_tool_result_content_and_marks_metadata():
    messages = _messages_with_tool_results(
        [
            ("old", "search", "A" * 50),
            ("kept", "search", "B" * 10),
            ("mid", "search", "D" * 30),
            ("recent", "search", "C" * 30),
        ]
    )

    result = prune_old_tool_outputs(
        messages,
        protect_recent_chars=12,
        min_pruned_chars=20,
        output_max_chars=8,
    )

    old_result = _tool_result(result.messages, "call-old")
    old_part = _tool_result_part(result.messages, "call-old")
    kept_result = _tool_result(result.messages, "call-kept")
    mid_result = _tool_result(result.messages, "call-mid")
    recent_result = _tool_result(result.messages, "call-recent")

    assert result.pruned_result_count == 1
    assert result.pruned_chars == 42
    assert result.protected_chars == 10
    assert old_result.content.startswith("A" * 8)
    assert "Old tool result content cleared for context compaction" in old_result.content
    assert "omitted 42 chars" in old_result.content
    assert old_result.output == old_result.content
    assert old_result.truncated is True
    assert old_result.metadata["compaction_pruned"] is True
    assert old_result.metadata["original_chars"] == 50
    assert old_result.metadata["omitted_chars"] == 42
    assert old_result.metadata["output_max_chars"] == 8
    assert "compaction_pruned_at" in old_result.metadata
    assert old_part.metadata["compaction_pruned"] is True
    assert old_part.metadata["omitted_chars"] == 42
    assert kept_result.content == "B" * 10
    assert mid_result.content == "D" * 30
    assert recent_result.content == "C" * 30


def test_protects_latest_two_user_turns():
    messages = _messages_with_tool_results(
        [
            ("old", "search", "A" * 30),
            ("second_latest", "search", "B" * 30),
            ("latest", "search", "C" * 30),
        ]
    )

    result = prune_old_tool_outputs(
        messages,
        protect_recent_chars=0,
        min_pruned_chars=0,
        output_max_chars=5,
    )

    assert result.pruned_result_count == 1
    assert _tool_result(result.messages, "call-old").metadata["compaction_pruned"] is True
    assert _tool_result(result.messages, "call-second_latest").content == "B" * 30
    assert _tool_result(result.messages, "call-latest").content == "C" * 30


def test_skips_skill_tool_results():
    messages = _messages_with_tool_results(
        [
            ("skill", "skill", "S" * 30),
            ("old", "search", "A" * 30),
            ("second_latest", "search", "B" * 30),
            ("latest", "search", "C" * 30),
        ]
    )

    result = prune_old_tool_outputs(
        messages,
        protect_recent_chars=0,
        min_pruned_chars=0,
        output_max_chars=5,
    )

    assert result.pruned_result_count == 1
    assert _tool_result(result.messages, "call-skill").content == "S" * 30
    assert "compaction_pruned" not in _tool_result(
        result.messages,
        "call-skill",
    ).metadata
    assert _tool_result(result.messages, "call-old").metadata["compaction_pruned"] is True
    assert result.metadata["skipped_protected_tool_count"] == 1


def test_noop_when_prunable_chars_do_not_pass_threshold():
    messages = _messages_with_tool_results(
        [
            ("old", "search", "A" * 10),
            ("second_latest", "search", "B" * 30),
            ("latest", "search", "C" * 30),
        ]
    )

    result = prune_old_tool_outputs(
        messages,
        protect_recent_chars=0,
        min_pruned_chars=10,
        output_max_chars=5,
    )

    assert result.pruned_result_count == 0
    assert result.pruned_chars == 0
    assert result.metadata["candidate_chars"] == 10
    assert _tool_result(result.messages, "call-old").content == "A" * 10


def test_does_not_mutate_input_messages():
    messages = _messages_with_tool_results(
        [
            ("old", "search", "A" * 30),
            ("second_latest", "search", "B" * 30),
            ("latest", "search", "C" * 30),
        ]
    )
    original_result = _tool_result(messages, "call-old")

    result = prune_old_tool_outputs(
        messages,
        protect_recent_chars=0,
        min_pruned_chars=0,
        output_max_chars=5,
    )

    assert original_result.content == "A" * 30
    assert original_result.output == "A" * 30
    assert original_result.metadata == {}
    assert result.messages is not messages
    assert _tool_result(result.messages, "call-old") is not original_result
    assert _tool_result(result.messages, "call-old").metadata["compaction_pruned"] is True


def test_agent_runtime_prune_session_tool_outputs_persists_changes_and_publishes_event():
    store = InMemorySessionStore()
    store.create_session(session_id="session-prune")
    _append_tool_turn(store, "session-prune", "old", "search", "A" * 30)
    _append_tool_turn(store, "session-prune", "second_latest", "search", "B" * 30)
    _append_tool_turn(store, "session-prune", "latest", "search", "C" * 30)
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        store=store,
        config=RuntimeConfig(
            compaction_prune=True,
            compaction_prune_protect_chars=0,
            compaction_prune_min_chars=0,
            compaction_tool_output_max_chars=5,
        ),
    )

    session = runtime.prune_session_tool_outputs("session-prune")

    persisted = store.read_history("session-prune")
    old_result = _tool_result(persisted, "call-old")
    assert session.messages[2].parts[0].tool_result.content == old_result.content
    assert old_result.metadata["compaction_pruned"] is True
    assert old_result.content.startswith("A" * 5)
    assert _tool_result(persisted, "call-second_latest").content == "B" * 30
    events = runtime.event_bus.history("session-prune")
    assert events[-1].type == "session_tool_outputs_pruned"
    assert events[-1].payload["pruned_result_count"] == 1
    assert events[-1].payload["pruned_chars"] == 25


def test_config_loader_maps_nested_prune_settings(tmp_path: Path):
    _write_json(
        tmp_path / "camel.json",
        {
            "compaction": {
                "prune": False,
                "toolOutputMaxChars": 123,
                "pruneMinChars": 456,
                "pruneProtectChars": 789,
            }
        },
    )
    _write_json(
        tmp_path / "snake.json",
        {
            "compaction": {
                "tool_output_max_chars": 321,
                "prune_min_chars": 654,
                "prune_protect_chars": 987,
            }
        },
    )

    camel = load_runtime_config(
        tmp_path,
        paths=["camel.json"],
        include_defaults=False,
    )
    snake = load_runtime_config(
        tmp_path,
        paths=["snake.json"],
        include_defaults=False,
    )

    assert camel.config.compaction_prune is False
    assert camel.config.compaction_tool_output_max_chars == 123
    assert camel.config.compaction_prune_min_chars == 456
    assert camel.config.compaction_prune_protect_chars == 789
    assert camel.metadata["unconsumed_config"] == {}
    assert snake.config.compaction_tool_output_max_chars == 321
    assert snake.config.compaction_prune_min_chars == 654
    assert snake.config.compaction_prune_protect_chars == 987
    assert snake.metadata["unconsumed_config"] == {}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"compaction_tool_output_max_chars": -1},
        {"compaction_prune_min_chars": -1},
        {"compaction_prune_protect_chars": -1},
        {"compaction_tool_output_max_chars": True},
    ],
)
def test_runtime_config_validates_compaction_prune_integer_settings(kwargs):
    with pytest.raises(ValueError, match="non-negative integer"):
        RuntimeConfig(**kwargs)


def _messages_with_tool_results(
    specs: list[tuple[str, str, str]],
) -> list[Message]:
    messages: list[Message] = []
    for label, tool_name, content in specs:
        messages.extend(_tool_turn_messages(label, tool_name, content))
    return messages


def _tool_turn_messages(label: str, tool_name: str, content: str) -> list[Message]:
    call = ToolCall(
        call_id=f"call-{label}",
        tool_name=tool_name,
        arguments={"label": label},
    )
    result = ToolResult(
        call_id=call.call_id,
        tool_name=tool_name,
        output=content,
        content=content,
        status="success",
        success=True,
    )
    return [
        Message(
            role=MessageRole.USER,
            message_id=f"msg-user-{label}",
            parts=[MessagePart.text_part(f"turn {label}")],
            status="complete",
        ),
        Message(
            role=MessageRole.ASSISTANT,
            message_id=f"msg-assistant-{label}",
            parts=[MessagePart.tool_call_part(call)],
            status="complete",
        ),
        Message(
            role=MessageRole.TOOL,
            message_id=f"msg-tool-{label}",
            parts=[MessagePart.tool_result_part(result)],
            status="complete",
        ),
    ]


def _append_tool_turn(
    store: InMemorySessionStore,
    session_id: str,
    label: str,
    tool_name: str,
    content: str,
) -> None:
    for message in _tool_turn_messages(label, tool_name, content):
        store.append_message(
            session_id,
            role=message.role,
            parts=message.parts,
            message_id=message.message_id,
            status=message.status,
        )


def _tool_result(messages: list[Message], call_id: str) -> ToolResult:
    return _tool_result_part(messages, call_id).tool_result


def _tool_result_part(messages: list[Message], call_id: str) -> MessagePart:
    for message in messages:
        for part in message.parts:
            if part.tool_result is not None and part.tool_result.call_id == call_id:
                return part
    raise AssertionError(f"tool result not found: {call_id}")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
