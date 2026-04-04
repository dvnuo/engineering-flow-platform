import pytest

from src.runtime.contracts import ExecutionResult, make_execution_request, make_execution_result
from src.runtime.execution_bus import ExecutionBus, build_default_execution_bus
from src.runtime.events import build_runtime_event


@pytest.mark.asyncio
async def test_execution_bus_dispatches_and_normalizes_dict_result():
    async def chat_handler(request):
        return {"response": f"echo:{request.input_payload.get('message', '')}"}

    bus = build_default_execution_bus(chat_handler=chat_handler)
    req = make_execution_request(
        source_type="chat",
        execution_type="chat",
        session_id="s1",
        input_payload={"message": "hello"},
    )

    result = await bus.execute(req)
    assert isinstance(result, ExecutionResult)
    assert result.status == "success"
    assert result.output_payload["response"] == "echo:hello"


@pytest.mark.asyncio
async def test_execution_bus_tool_handler_preserves_tool_result_shape():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="system",
        execution_type="tool",
        input_payload={"tool_name": "run_command", "kwargs": {"command": "echo execution-bus"}},
    )

    result = await bus.execute(req)
    assert result.status in {"success", "error"}
    assert set(result.output_payload.keys()) == {"success", "content", "error"}


@pytest.mark.asyncio
async def test_execution_bus_emits_additive_runtime_event():
    emitted = []

    async def chat_handler(_request):
        return {"response": "ok"}

    bus = build_default_execution_bus(
        chat_handler=chat_handler,
        event_emitter=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    req = make_execution_request(source_type="chat", execution_type="chat", session_id="s2")
    await bus.execute(req)

    assert emitted
    event_type, payload = emitted[0]
    assert event_type == "execution_completed"
    assert payload["event_type"] == "execution.completed"
    assert payload["type"] == "execution_completed"


def test_runtime_event_builder_is_additive_with_legacy_payload():
    event = build_runtime_event(
        event_type="runtime.update",
        state="success",
        session_id="s3",
        request_id="r3",
        agent_id="a3",
        summary="done",
        detail_payload={"k": "v"},
        legacy_payload={"type": "legacy", "data": {"k": "v"}},
    )

    assert event["detail_payload"] == {"k": "v"}
    assert event["type"] == "legacy"
    assert event["data"] == {"k": "v"}


def test_make_execution_result_defaults():
    result = make_execution_result(request_id="r1", status="success")
    assert result.output_payload == {}
    assert result.artifacts == {}
    assert result.runtime_events == []
