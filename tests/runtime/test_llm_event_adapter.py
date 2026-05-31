import pytest

from efp_runtime.llm.adapter import DefaultLLMEventAdapter
from efp_runtime.llm.events import LLMEventType


def test_non_streaming_response_normalizes_two_tool_calls():
    adapter = DefaultLLMEventAdapter()
    response = {
        "content": "",
        "tool_calls": [
            {
                "id": "call_a",
                "type": "function",
                "function": {"name": "search", "arguments": '{"query": "efp"}'},
            },
            {
                "id": "call_b",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
            },
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }

    events = list(adapter.normalize_response(response))
    completed = [event.tool_call for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE]

    assert [event.type for event in events][:2] == [
        LLMEventType.STEP_START,
        LLMEventType.MESSAGE_START,
    ]
    assert len(completed) == 2
    assert completed[0].call_id == "call_a"
    assert completed[0].tool_name == "search"
    assert completed[0].arguments == {"query": "efp"}
    assert completed[1].call_id == "call_b"
    assert completed[1].tool_name == "read_file"
    assert completed[1].arguments == {"path": "README.md"}
    assert events[-1].type == LLMEventType.STEP_FINISH
    assert events[-1].usage["total_tokens"] == 12


def test_function_calls_and_tool_calls_are_deduplicated():
    adapter = DefaultLLMEventAdapter()
    response = {
        "function_calls": [
            {"call_id": "call_a", "name": "search", "arguments": '{"query": "efp"}'},
        ],
        "tool_calls": [
            {
                "id": "call_a",
                "type": "function",
                "function": {"name": "search", "arguments": '{"query": "efp"}'},
            },
        ],
    }

    events = list(adapter.normalize_response(response))
    completed = [event.tool_call for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE]

    assert len(completed) == 1
    assert completed[0].call_id == "call_a"
    assert completed[0].arguments == {"query": "efp"}


@pytest.mark.asyncio
async def test_streaming_chat_chunks_emit_text_deltas():
    adapter = DefaultLLMEventAdapter()
    chunks = [
        {"choices": [{"delta": {"content": "Hello"}}]},
        {"choices": [{"delta": {"content": " world"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 7}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]
    text_events = [event for event in events if event.type == LLMEventType.TEXT_DELTA]

    assert [event.delta for event in text_events] == ["Hello", " world"]
    assert events[-1].type == LLMEventType.STEP_FINISH
    assert events[-1].usage == {"total_tokens": 7}
