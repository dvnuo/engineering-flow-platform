import pytest

from efp_runtime import ToolCall, ToolResult
from efp_runtime.llm.adapter import DefaultLLMEventAdapter
from efp_runtime.llm.events import LLMEvent, LLMEventType
from efp_runtime.session.processor import RuntimeSession, SessionProcessor


@pytest.mark.asyncio
async def test_text_deltas_become_final_text_part():
    processor = SessionProcessor(RuntimeSession(session_id="s-text"))

    message = await processor.consume(
        [
            LLMEvent(LLMEventType.STEP_START),
            LLMEvent(LLMEventType.MESSAGE_START, message_id="msg_1"),
            LLMEvent(LLMEventType.TEXT_START, part_id="text_1"),
            LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_1", delta="Hello"),
            LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_1", delta=" world"),
            LLMEvent(LLMEventType.TEXT_END, part_id="text_1"),
            LLMEvent(LLMEventType.STEP_FINISH),
        ]
    )

    assert message is not None
    assert message.role.value == "assistant"
    assert message.status == "complete"
    assert len(message.parts) == 1
    assert message.parts[0].type.value == "text"
    assert message.parts[0].text == "Hello world"


@pytest.mark.asyncio
async def test_non_streaming_two_tool_calls_become_assistant_parts():
    adapter = DefaultLLMEventAdapter()
    processor = SessionProcessor(RuntimeSession(session_id="s-tools"))
    response = {
        "content": "",
        "tool_calls": [
            {
                "id": "call_a",
                "type": "function",
                "function": {"name": "search", "arguments": '{"query": "EFP runtime"}'},
            },
            {
                "id": "call_b",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "src/README.md"}'},
            },
        ],
    }

    message = await processor.consume(adapter.normalize_response(response))

    assert message is not None
    assert message in processor.session.messages
    assert message.role.value == "assistant"
    assert [part.type.value for part in message.parts] == ["tool_call", "tool_call"]
    assert message.parts[0].tool_call.call_id == "call_a"
    assert message.parts[0].tool_call.tool_name == "search"
    assert message.parts[0].tool_call.arguments == {"query": "EFP runtime"}
    assert message.parts[1].tool_call.call_id == "call_b"
    assert message.parts[1].tool_call.tool_name == "read_file"
    assert message.parts[1].tool_call.arguments == {"path": "src/README.md"}


@pytest.mark.asyncio
async def test_tool_result_then_step_finish_keeps_usage_on_assistant_message():
    processor = SessionProcessor(RuntimeSession(session_id="s-result"))
    call = ToolCall(tool_name="search", arguments={"query": "runtime"}, call_id="call-1")
    result = ToolResult(call_id="call-1", tool_name="search", output="found")

    message = await processor.consume(
        [
            LLMEvent(LLMEventType.STEP_START),
            LLMEvent(LLMEventType.MESSAGE_START, message_id="assistant-1"),
            LLMEvent(LLMEventType.TOOL_CALL_COMPLETE, tool_call=call),
            LLMEvent(LLMEventType.TOOL_RESULT, tool_result=result),
            LLMEvent(LLMEventType.STEP_FINISH, usage={"total_tokens": 9}),
        ]
    )

    assistant_message = processor.session.messages[0]
    tool_message = processor.session.messages[1]

    assert message is assistant_message
    assert assistant_message.role.value == "assistant"
    assert assistant_message.status == "complete"
    assert assistant_message.usage == {"total_tokens": 9}
    assert tool_message.role.value == "tool"
    assert tool_message.status == "complete"
    assert tool_message.usage == {}
    assert tool_message.parts[0].tool_result.call_id == "call-1"


@pytest.mark.asyncio
async def test_streaming_tool_input_updates_single_tool_part_state_to_completed():
    processor = SessionProcessor(RuntimeSession(session_id="s-stream-tool"))
    call = ToolCall(
        tool_name="grep",
        arguments={"pattern": "runtime"},
        call_id="call-stream",
        arguments_text='{"pattern": "runtime"}',
    )
    result = ToolResult(
        call_id="call-stream",
        tool_name="grep",
        output={"matches": 2},
        metadata={"title": "Grep", "phase": "done"},
    )

    message = await processor.consume(
        [
            LLMEvent(LLMEventType.STEP_START),
            LLMEvent(LLMEventType.MESSAGE_START, message_id="assistant-stream"),
            LLMEvent(
                LLMEventType.TOOL_INPUT_START,
                tool_call_id="call-stream",
                tool_name="grep",
            ),
            LLMEvent(
                LLMEventType.TOOL_INPUT_DELTA,
                tool_call_id="call-stream",
                tool_name="grep",
                delta='{"pattern"',
            ),
            LLMEvent(
                LLMEventType.TOOL_INPUT_DELTA,
                tool_call_id="call-stream",
                tool_name="grep",
                delta=': "runtime"}',
            ),
            LLMEvent(
                LLMEventType.TOOL_INPUT_END,
                tool_call_id="call-stream",
                tool_name="grep",
            ),
            LLMEvent(LLMEventType.TOOL_CALL_COMPLETE, tool_call=call),
            LLMEvent(LLMEventType.TOOL_RESULT, tool_result=result),
            LLMEvent(LLMEventType.STEP_FINISH),
        ]
    )

    assert message is processor.session.messages[0]
    assistant_message = processor.session.messages[0]
    tool_parts = [
        part for part in assistant_message.parts if part.type.value == "tool_call"
    ]
    assert len(tool_parts) == 1
    part = tool_parts[0]
    assert part.tool_call.call_id == "call-stream"
    assert part.tool_call.status == "completed"
    state = part.metadata["tool_state"]
    assert state["status"] == "completed"
    assert state["raw"] == '{"pattern": "runtime"}'
    assert state["input"] == {"pattern": "runtime"}
    assert state["output"] == {"matches": 2}
    assert state["metadata"] == {"title": "Grep", "phase": "done"}
    assert state["title"] == "Grep"
    assert state["input_ended"] is True
    assert set(state["time"]) == {"start", "end"}
    assert processor.session.messages[1].role.value == "tool"
    assert processor.session.messages[1].parts[0].tool_result.call_id == "call-stream"


@pytest.mark.asyncio
async def test_tool_call_complete_without_input_start_creates_running_then_completed_part():
    processor = SessionProcessor(RuntimeSession(session_id="s-direct-tool"))
    call = ToolCall(
        tool_name="read",
        arguments={"path": "README.md"},
        call_id="call-direct",
    )
    result = ToolResult(
        call_id="call-direct",
        tool_name="read",
        output="contents",
    )

    await processor.consume(
        [
            LLMEvent(LLMEventType.STEP_START),
            LLMEvent(LLMEventType.MESSAGE_START, message_id="assistant-direct"),
            LLMEvent(LLMEventType.TOOL_CALL_COMPLETE, tool_call=call),
            LLMEvent(LLMEventType.TOOL_RESULT, tool_result=result),
            LLMEvent(LLMEventType.STEP_FINISH),
        ]
    )

    assistant_message = processor.session.messages[0]
    assert len(assistant_message.parts) == 1
    part = assistant_message.parts[0]
    assert part.tool_call.call_id == "call-direct"
    assert part.tool_call.status == "completed"
    state = part.metadata["tool_state"]
    assert state["status"] == "completed"
    assert state["input"] == {"path": "README.md"}
    assert state["output"] == "contents"
