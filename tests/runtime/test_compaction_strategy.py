from efp_runtime.compaction.strategy import PartAwareCompactionStrategy
from efp_runtime.models import Message, MessagePart, ToolCall, ToolResult


def _tool_pair(call_id="call-1"):
    call = ToolCall(call_id=call_id, tool_name="search", arguments={"query": "runtime"})
    result = ToolResult(call_id=call_id, tool_name="search", status="success", content="result")
    return (
        Message(role="assistant", parts=[MessagePart.tool_call_part(call)]),
        Message(role="tool", parts=[MessagePart.tool_result_part(result)]),
    )


def _tool_part_ids(messages):
    calls = []
    results = []
    for message in messages:
        for part in message.parts:
            if part.type == "tool_call" and part.tool_call is not None:
                calls.append(part.tool_call.call_id)
            if part.type == "tool_result" and part.tool_result is not None:
                results.append(part.tool_result.call_id)
    return calls, results


def test_compaction_summarizes_tool_pair_together_when_pair_is_old():
    call_message, result_message = _tool_pair()
    messages = [
        Message.from_text("user", "old request"),
        call_message,
        result_message,
        Message.from_text("assistant", "recent answer"),
        Message.from_text("user", "latest request"),
    ]

    compacted = PartAwareCompactionStrategy(max_parts=3).compact(messages)

    assert compacted.compacted is True
    assert compacted.messages[0].parts[0].type == "compaction"
    assert compacted.messages[0].parts[0].compaction.tool_pair_count == 1
    calls, results = _tool_part_ids(compacted.messages)
    assert calls == []
    assert results == []
    assert [message.role.value for message in compacted.messages[1:]] == ["assistant", "user"]


def test_compaction_keeps_tool_pair_together_when_pair_fits_recent_budget():
    call_message, result_message = _tool_pair()
    messages = [
        Message.from_text("user", "old request"),
        Message.from_text("assistant", "old answer"),
        call_message,
        result_message,
        Message.from_text("user", "latest request"),
    ]

    compacted = PartAwareCompactionStrategy(max_parts=4).compact(messages)

    calls, results = _tool_part_ids(compacted.messages)
    assert calls == ["call-1"]
    assert results == ["call-1"]
    assert compacted.compacted_part_count == 2
    assert compacted.messages[0].parts[0].type == "compaction"


def test_compaction_never_leaves_one_side_of_tool_pair():
    call_message, result_message = _tool_pair()
    messages = [
        Message.from_text("user", "old request"),
        call_message,
        result_message,
        Message.from_text("user", "latest request"),
    ]

    compacted = PartAwareCompactionStrategy(max_parts=3).compact(messages)
    calls, results = _tool_part_ids(compacted.messages)

    assert calls == results


def test_compaction_leaves_history_unchanged_when_under_budget():
    messages = [
        Message.from_text("user", "hello"),
        Message.from_text("assistant", "hi"),
    ]

    compacted = PartAwareCompactionStrategy(max_parts=3).compact(messages)

    assert compacted.compacted is False
    assert compacted.messages == messages
