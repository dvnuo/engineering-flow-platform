import pytest

from efp_runtime.llm.adapter import DefaultLLMEventAdapter
from efp_runtime.llm.events import LLMEventType
from efp_runtime.session.models import MessagePartType
from efp_runtime.session.processor import RuntimeSession, SessionProcessor
from efp_runtime.session.status import RuntimeStatus


def _assert_responses_finished(events, usage=None):
    assert events[-2].type == LLMEventType.STEP_FINISH
    assert events[-1].type == LLMEventType.FINISH
    if usage is not None:
        assert events[-2].usage == usage
        assert events[-1].usage == usage


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


@pytest.mark.asyncio
async def test_streaming_responses_chunks_emit_text_reasoning_and_tool_input_events():
    adapter = DefaultLLMEventAdapter()
    chunks = [
        {"type": "response.output_text.delta", "delta": "Hello"},
        {"type": "response.reasoning_summary_text.delta", "delta": "Thinking"},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item_id": "fc_1",
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_search", "name": "search", "arguments": ""},
        },
        {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_1", "delta": '{"query":'},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_1", "delta": '"efp"}'},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item_id": "fc_1",
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_search",
                "name": "search",
                "arguments": '{"query":"efp"}',
            },
        },
        {"type": "response.completed", "usage": {"total_tokens": 12}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]

    assert [event.delta for event in events if event.type == LLMEventType.TEXT_DELTA] == ["Hello"]
    assert [event.delta for event in events if event.type == LLMEventType.REASONING_DELTA] == ["Thinking"]
    tool_input_events = [
        event.type for event in events
        if event.type in {
            LLMEventType.TOOL_INPUT_START,
            LLMEventType.TOOL_INPUT_DELTA,
            LLMEventType.TOOL_INPUT_END,
            LLMEventType.TOOL_CALL_COMPLETE,
        }
    ]
    assert tool_input_events == [
        LLMEventType.TOOL_INPUT_START,
        LLMEventType.TOOL_INPUT_DELTA,
        LLMEventType.TOOL_INPUT_DELTA,
        LLMEventType.TOOL_INPUT_END,
        LLMEventType.TOOL_CALL_COMPLETE,
    ]
    completed = next(event.tool_call for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE)
    assert completed.call_id == "call_search"
    assert completed.tool_name == "search"
    assert completed.arguments == {"query": "efp"}
    _assert_responses_finished(events, {"total_tokens": 12})


@pytest.mark.asyncio
async def test_streaming_responses_function_arguments_done_and_item_done_complete_once():
    adapter = DefaultLLMEventAdapter()
    chunks = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item_id": "fc_1",
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_search",
                "name": "search",
                "arguments": "",
            },
        },
        {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_1", "delta": '{"query":'},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_1", "delta": '"efp"}'},
        {
            "type": "response.function_call_arguments.done",
            "output_index": 0,
            "item_id": "fc_1",
            "arguments": '{"query":"efp"}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item_id": "fc_1",
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_search",
                "name": "search",
                "arguments": '{"query":"efp"}',
            },
        },
        {"type": "response.completed", "usage": {"total_tokens": 12}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]

    assert [event.type for event in events].count(LLMEventType.TOOL_INPUT_START) == 1
    assert [event.type for event in events].count(LLMEventType.TOOL_INPUT_END) == 1
    complete_events = [
        event for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE
    ]
    assert len(complete_events) == 1
    assert [event.delta for event in events if event.type == LLMEventType.TOOL_INPUT_DELTA] == [
        '{"query":',
        '"efp"}',
    ]
    assert complete_events[0].tool_call.call_id == "call_search"
    assert complete_events[0].tool_call.arguments == {"query": "efp"}


@pytest.mark.asyncio
async def test_streaming_responses_output_item_nested_function_name_completes_tool_call():
    adapter = DefaultLLMEventAdapter()
    chunks = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item_id": "fc_1",
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_search",
                "function": {"name": "search"},
                "arguments": "",
            },
        },
        {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_1", "delta": '{"query":'},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_1", "delta": '"efp"}'},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item_id": "fc_1",
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_search",
                "function": {"name": "search"},
                "arguments": '{"query":"efp"}',
            },
        },
        {"type": "response.completed", "usage": {"total_tokens": 12}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]
    complete_events = [
        event for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE
    ]

    assert len(complete_events) == 1
    assert complete_events[0].tool_call.call_id == "call_search"
    assert complete_events[0].tool_call.tool_name == "search"
    assert complete_events[0].tool_call.arguments == {"query": "efp"}


@pytest.mark.asyncio
async def test_streaming_responses_orphan_function_call_arguments_are_ignored():
    adapter = DefaultLLMEventAdapter()
    chunks = [
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_missing_name",
            "delta": '{"query":"efp"}',
        },
        {
            "type": "response.function_call_arguments.done",
            "item_id": "fc_missing_name",
            "arguments": '{"query":"efp"}',
        },
        {"type": "response.completed", "usage": {"total_tokens": 12}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]
    complete_events = [
        event for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE
    ]
    error_events = [event for event in events if event.type == LLMEventType.ERROR]
    provider_error_events = [
        event for event in events if event.type == LLMEventType.PROVIDER_ERROR
    ]

    assert complete_events == []
    assert error_events == []
    assert provider_error_events == []
    assert all(event.tool_call_id != "fc_missing_name" for event in events)
    _assert_responses_finished(events, {"total_tokens": 12})


@pytest.mark.asyncio
async def test_streaming_responses_orphan_encrypted_item_id_arguments_are_ignored():
    adapter = DefaultLLMEventAdapter()
    encrypted_item_id = (
        "zWKh9D/etpetECfeACpT2ONLWvuJm9K3bvtlp5KzCsZasSD0KlemPHSlmZWvSrujW+"
        "xIkWK0pts86n/TlXVf+kzTF4DP4h1wwnpZSgt+8bElTmnXob8QuFenDahD1AvaGsTQY"
        "OBnNj7N1JXs5F1Gbc2PBIs99duvBNM4dMC15mFJtaQjKg9vMBeuT/cMOEeGP24A4Nvtf"
        "WdlFquq2WAOLUf/bZLT0orolyXrZpRq/hy8Sb1Oa+a7RPsalyrusQ6MEmS7W35J7bZq"
        "D7vGZT2Zd1fNRWPNJ0M9vW8YtaPrSGe4In+Ai/xPNyuSiOBB1Z6tL/1luzi5lKD6Z3ZsGSEp6MmJ8sLzhR4uc1PYHBCMcrhLwwdUTCSPb82rwmN4JkyU+tcAwo4lZ8qChBDnutDUnw=="
    )
    chunks = [
        {
            "type": "response.function_call_arguments.delta",
            "item_id": encrypted_item_id,
            "delta": '{"query":"efp"}',
        },
        {
            "type": "response.function_call_arguments.done",
            "item_id": encrypted_item_id,
            "arguments": '{"query":"efp"}',
        },
        {"type": "response.completed", "usage": {"total_tokens": 12}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]

    assert [event for event in events if event.type == LLMEventType.ERROR] == []
    assert [event for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE] == []
    assert encrypted_item_id not in {
        event.tool_call_id for event in events if event.tool_call_id
    }
    _assert_responses_finished(events, {"total_tokens": 12})


@pytest.mark.asyncio
async def test_streaming_responses_function_call_uses_output_index_not_encrypted_item_id():
    adapter = DefaultLLMEventAdapter()
    encrypted_added_item_id = "encrypted-added"
    encrypted_delta_item_id = (
        "zWKh9D/rotatingEncryptedItemIdThatChangesOnEachCopilotResponsesEvent"
        "uSiOBB1Z6tL/1luzi5lKD6Z3ZsGSEp6MmJ8sLzhR4uc1PYHBCMcrhLwwdUTCSPb82rwmN4JkyU+tc=="
    )
    chunks = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item_id": encrypted_added_item_id,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_search",
                "name": "search",
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "item_id": encrypted_delta_item_id,
            "delta": '{"query":"efp"}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item_id": "encrypted-done",
            "item": {
                "type": "function_call",
                "id": "fc_done",
                "call_id": "call_search",
                "name": "search",
                "arguments": '{"query":"efp"}',
            },
        },
        {"type": "response.completed", "usage": {"total_tokens": 12}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]
    tool_events = [
        event for event in events
        if event.type in {
            LLMEventType.TOOL_INPUT_START,
            LLMEventType.TOOL_INPUT_DELTA,
            LLMEventType.TOOL_INPUT_END,
            LLMEventType.TOOL_CALL_COMPLETE,
        }
    ]

    assert [event.type for event in tool_events] == [
        LLMEventType.TOOL_INPUT_START,
        LLMEventType.TOOL_INPUT_DELTA,
        LLMEventType.TOOL_INPUT_END,
        LLMEventType.TOOL_CALL_COMPLETE,
    ]
    assert {event.tool_call_id for event in tool_events} == {"call_search"}
    assert encrypted_added_item_id not in {event.tool_call_id for event in tool_events}
    assert encrypted_delta_item_id not in {event.tool_call_id for event in tool_events}
    assert [event for event in events if event.type == LLMEventType.ERROR] == []
    complete_event = next(
        event for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE
    )
    assert len([event for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE]) == 1
    assert complete_event.tool_name == "search"
    assert complete_event.tool_call.tool_name == "search"
    assert complete_event.tool_call.arguments == {"query": "efp"}


@pytest.mark.asyncio
async def test_streaming_responses_standard_item_id_arguments_use_final_item_arguments():
    adapter = DefaultLLMEventAdapter()
    chunks = [
        {
            "type": "response.output_item.added",
            "item_id": "fc_1",
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_search",
                "name": "search",
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": '{"query":"wrong"}',
        },
        {
            "type": "response.output_item.done",
            "item_id": "fc_1",
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_search",
                "name": "search",
                "arguments": '{"query":"efp","limit":2}',
            },
        },
        {"type": "response.completed", "response": {"usage": {"total_tokens": 12}}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]
    complete_events = [
        event for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE
    ]

    assert len(complete_events) == 1
    assert [event.delta for event in events if event.type == LLMEventType.TOOL_INPUT_DELTA] == [
        '{"query":"wrong"}',
    ]
    assert complete_events[0].tool_call.call_id == "call_search"
    assert complete_events[0].tool_call.tool_name == "search"
    assert complete_events[0].tool_call.arguments == {"query": "efp", "limit": 2}
    assert complete_events[0].tool_call.arguments_text == '{"query":"efp","limit":2}'
    _assert_responses_finished(events, {"total_tokens": 12})


@pytest.mark.asyncio
async def test_streaming_responses_delta_before_output_item_added_is_ignored():
    adapter = DefaultLLMEventAdapter()
    chunks = [
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "item_id": "encrypted-before-added",
            "delta": '{"ignored":true}',
        },
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item_id": "encrypted-added",
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_search",
                "name": "search",
                "arguments": "",
            },
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item_id": "encrypted-done",
            "item": {
                "type": "function_call",
                "id": "fc_done",
                "call_id": "call_search",
                "name": "search",
                "arguments": '{"query":"efp"}',
            },
        },
        {"type": "response.completed", "usage": {"total_tokens": 12}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]
    complete_event = next(
        event for event in events if event.type == LLMEventType.TOOL_CALL_COMPLETE
    )

    assert [event.delta for event in events if event.type == LLMEventType.TOOL_INPUT_DELTA] == [
        '{"query":"efp"}',
    ]
    assert [event for event in events if event.type == LLMEventType.ERROR] == []
    assert complete_event.tool_call.call_id == "call_search"
    assert complete_event.tool_call.arguments == {"query": "efp"}


@pytest.mark.asyncio
async def test_streaming_responses_failed_and_top_level_error_emit_provider_error():
    adapter = DefaultLLMEventAdapter()
    failed_chunks = [
        {
            "type": "response.failed",
            "response": {
                "id": "resp_failed",
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "Slow down",
                },
            },
        }
    ]
    top_level_chunks = [
        {
            "type": "error",
            "code": "context_length_exceeded",
            "message": "Too many tokens",
            "retryable": False,
        }
    ]

    failed_events = [event async for event in adapter.normalize_stream(failed_chunks)]
    top_level_events = [event async for event in adapter.normalize_stream(top_level_chunks)]

    failed_provider_error = next(
        event for event in failed_events if event.type == LLMEventType.PROVIDER_ERROR
    )
    top_level_provider_error = next(
        event for event in top_level_events if event.type == LLMEventType.PROVIDER_ERROR
    )

    assert failed_provider_error.error == "rate_limit_exceeded: Slow down"
    assert failed_provider_error.metadata["code"] == "rate_limit_exceeded"
    assert failed_provider_error.metadata["response_id"] == "resp_failed"
    assert top_level_provider_error.error == "context_length_exceeded: Too many tokens"
    assert top_level_provider_error.metadata["retryable"] is False
    assert [event for event in failed_events if event.type == LLMEventType.STEP_FINISH] == []
    assert [event for event in top_level_events if event.type == LLMEventType.STEP_FINISH] == []

    processor = SessionProcessor(RuntimeSession(session_id="s-provider-error"))
    message = await processor.consume(failed_events)

    assert processor.session.status is RuntimeStatus.ERROR
    assert message.status == "error"
    assert message.parts[-1].type is MessagePartType.ERROR
    assert message.parts[-1].text == "rate_limit_exceeded: Slow down"
    assert message.parts[-1].metadata["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_streaming_responses_reasoning_item_lifecycle_updates_session_part():
    adapter = DefaultLLMEventAdapter()
    chunks = [
        {
            "type": "response.output_item.added",
            "item_id": "rs_1",
            "item": {
                "type": "reasoning",
                "id": "rs_1",
                "encrypted_content": "enc_reasoning",
            },
        },
        {
            "type": "response.reasoning_summary_text.delta",
            "item_id": "rs_1",
            "summary_index": 0,
            "delta": "Thinking",
        },
        {
            "type": "response.output_item.done",
            "item_id": "rs_1",
            "item": {
                "type": "reasoning",
                "id": "rs_1",
                "encrypted_content": "enc_reasoning",
            },
        },
        {"type": "response.completed", "response": {"usage": {"total_tokens": 9}}},
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]
    event_types = [event.type for event in events]

    assert LLMEventType.REASONING_START in event_types
    assert [event.delta for event in events if event.type == LLMEventType.REASONING_DELTA] == [
        "Thinking",
    ]
    assert LLMEventType.REASONING_END in event_types
    reasoning_start = next(
        event for event in events if event.type == LLMEventType.REASONING_START
    )
    assert reasoning_start.metadata["item_id"] == "rs_1"
    assert reasoning_start.metadata["encrypted_content"] == "enc_reasoning"
    _assert_responses_finished(events, {"total_tokens": 9})

    processor = SessionProcessor(RuntimeSession(session_id="s-reasoning"))
    message = await processor.consume(events)
    reasoning_parts = [
        part for part in message.parts if part.type is MessagePartType.REASONING
    ]

    assert len(reasoning_parts) == 1
    assert reasoning_parts[0].reasoning == "Thinking"
    assert reasoning_parts[0].text == "Thinking"
    assert reasoning_parts[0].metadata["item_id"] == "rs_1"
    assert reasoning_parts[0].metadata["encrypted_content"] == "enc_reasoning"


@pytest.mark.asyncio
async def test_streaming_responses_completed_emits_step_finish_and_finish_with_usage():
    adapter = DefaultLLMEventAdapter()
    chunks = [
        {"type": "response.output_text.delta", "delta": "Done"},
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "service_tier": "default",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 4,
                    "total_tokens": 7,
                },
            },
        },
    ]

    events = [event async for event in adapter.normalize_stream(chunks)]
    step_finish = next(event for event in events if event.type == LLMEventType.STEP_FINISH)
    finish = next(event for event in events if event.type == LLMEventType.FINISH)

    assert events[-2].type == LLMEventType.STEP_FINISH
    assert events[-1].type == LLMEventType.FINISH
    assert step_finish.usage == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
    assert finish.usage == step_finish.usage
    assert step_finish.metadata["finish_reason"] == "stop"
    assert step_finish.metadata["response_id"] == "resp_1"
    assert step_finish.provider_metadata["openai"]["responseId"] == "resp_1"
