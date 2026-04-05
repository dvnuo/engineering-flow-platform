import pytest

from src import ToolResult
from src.agents.errors import LLMError
from src.runtime.contracts import ExecutionResult, make_execution_request, make_execution_result
from src.runtime.execution_bus import ExecutionBus, build_default_execution_bus
from src.runtime.events import build_runtime_event
from src.runtime.governance import GovernanceHooks


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
async def test_execution_bus_preserves_explicit_dict_status():
    async def chat_handler(_request):
        return {"status": "queued", "response": "later"}

    bus = build_default_execution_bus(chat_handler=chat_handler)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)
    assert result.status == "queued"


@pytest.mark.asyncio
async def test_execution_bus_tool_handler_preserves_tool_result_shape(monkeypatch):
    async def _fake_execute_tool_by_name(_name, **_kwargs):
        return ToolResult(success=True, content="ok", error=None)

    monkeypatch.setattr("src.runtime.execution_bus.execute_tool_by_name", _fake_execute_tool_by_name)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="system",
        execution_type="tool",
        input_payload={"tool_name": "run_command", "kwargs": {"cmd": "echo", "args": ["execution-bus"]}},
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
    assert event_type == "execution_started"
    assert payload["event_type"] == "execution.started"
    assert payload["legacy_type"] == "execution_started"
    assert emitted[1][0] == "execution_completed"
    assert emitted[1][1]["event_type"] == "execution.completed"
    assert emitted[0][1]["event"] == "execution.started"
    assert emitted[0][1]["execution_id"] == emitted[0][1]["request_id"]
    assert emitted[0][1]["type"] == "chat"
    assert emitted[0][1]["payload"]["execution_type"] == "chat"
    assert emitted[0][1]["timestamp"] == emitted[0][1]["created_at"]


@pytest.mark.asyncio
async def test_execution_bus_emits_failed_lifecycle_event():
    emitted = []

    async def bad_handler(_request):
        raise RuntimeError("boom")

    bus = ExecutionBus(event_emitter=lambda event_type, payload: emitted.append((event_type, payload)))
    bus.register_handler("chat", bad_handler)
    req = make_execution_request(source_type="chat", execution_type="chat", session_id="s3")
    result = await bus.execute(req)

    assert result.status == "error"
    event_names = [name for name, _payload in emitted]
    assert event_names == ["execution_started", "execution_failed"]
    assert emitted[1][1]["detail_payload"]["status"] == "error"


@pytest.mark.asyncio
async def test_execution_bus_preserves_structured_llm_error_fields():
    async def chat_handler(_request):
        raise LLMError(
            message="Provider failed",
            error_type="bad_request",
            details={"code": "x1"},
            provider="openai",
            status_code=429,
        )

    bus = build_default_execution_bus(chat_handler=chat_handler)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "Provider failed"
    assert result.output_payload["error_type"] == "bad_request"
    assert result.output_payload["exception_class"] == "LLMError"
    assert result.output_payload["status_code"] == 429
    assert result.output_payload["details"] == {"code": "x1"}
    assert result.output_payload["provider"] == "openai"


@pytest.mark.asyncio
async def test_execution_bus_generic_exception_keeps_backward_compatible_error_payload():
    async def chat_handler(_request):
        raise RuntimeError("boom")

    bus = build_default_execution_bus(chat_handler=chat_handler)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "boom"
    assert result.output_payload["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_execution_bus_normalized_error_result_emits_failed_event():
    emitted = []

    async def chat_handler(_request):
        return {"error": "nope"}

    bus = build_default_execution_bus(
        chat_handler=chat_handler,
        event_emitter=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)
    assert result.status == "error"
    assert [name for name, _ in emitted] == ["execution_started", "execution_failed"]


@pytest.mark.asyncio
async def test_execution_bus_blocked_result_emits_failed_event():
    emitted = []
    bus = build_default_execution_bus(event_emitter=lambda event_type, payload: emitted.append((event_type, payload)))
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)
    assert result.status == "blocked"
    assert [name for name, _ in emitted] == ["execution_started", "execution_failed"]


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
    assert event["event"] == "runtime.update"
    assert event["execution_id"] == "r3"
    assert event["timestamp"] == event["created_at"]


def test_runtime_event_builder_calls_utcnow_once(monkeypatch):
    from datetime import datetime as _datetime
    from src.runtime import events as events_module

    class _DatetimeStub:
        calls = 0

        @classmethod
        def utcnow(cls):
            cls.calls += 1
            return _datetime(2026, 1, 1, 0, 0, 0)

    monkeypatch.setattr(events_module, "datetime", _DatetimeStub)
    event = events_module.build_runtime_event(
        event_type="runtime.update",
        state="ok",
        session_id="s1",
        request_id="r1",
        agent_id="a1",
        summary="ok",
        detail_payload={"execution_type": "chat"},
    )
    assert _DatetimeStub.calls == 1
    assert event["timestamp"] == event["created_at"]


@pytest.mark.asyncio
async def test_execution_bus_invokes_governance_hooks():
    class _RecordingGovernance(GovernanceHooks):
        def __init__(self):
            self.calls = []

        def before_execute(self, request):
            self.calls.append(("before", request.request_id))
            return {}

        def after_execute(self, request, result):
            self.calls.append(("after", result.status))
            return {}

        def on_error(self, request, error):
            self.calls.append(("error", error.__class__.__name__))
            return {}

    async def _ok(_request):
        return {"response": "ok"}

    governance = _RecordingGovernance()
    bus = ExecutionBus(governance=governance)
    bus.register_handler("chat", _ok)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)

    assert result.status == "success"
    assert governance.calls == [("before", req.request_id), ("after", "success")]


@pytest.mark.asyncio
async def test_execution_bus_governance_on_error_invoked():
    class _RecordingGovernance(GovernanceHooks):
        def __init__(self):
            self.error_seen = None

        def on_error(self, request, error):
            self.error_seen = error.__class__.__name__
            return {}

    async def _bad(_request):
        raise RuntimeError("boom")

    governance = _RecordingGovernance()
    bus = ExecutionBus(governance=governance)
    bus.register_handler("chat", _bad)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)

    assert result.status == "error"
    assert governance.error_seen == "RuntimeError"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_tool_task(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return ToolResult(success=True, content=f"{tool_name}:{session_id}", error=None)

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {"x": 1}},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["task_type"] == "tool_task"
    assert result.output_payload["tool_name"] == "demo_tool"
    assert result.output_payload["task_boundary"] is True
    assert result.output_payload["success"] is True


@pytest.mark.asyncio
async def test_execution_bus_task_handler_unsupported_task_type():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "unknown_task"},
    )
    result = await bus.execute(req)
    assert result.status == "blocked"
    assert result.output_payload["success"] is False


@pytest.mark.asyncio
async def test_execution_bus_uses_injected_execute_tool_callable_for_tool_and_task():
    calls = []

    async def _injected_execute(tool_name, **kwargs):
        calls.append((tool_name, kwargs))
        return ToolResult(success=True, content=f"injected:{tool_name}", error=None)

    async def _inline_task_runner(*, session_id, tool_name, coro_factory, event_callback=None):
        return await coro_factory()

    bus = build_default_execution_bus(execute_tool_func=_injected_execute)
    req_tool = make_execution_request(
        source_type="agent",
        execution_type="tool",
        session_id="s-injected",
        input_payload={"tool_name": "tool_a", "kwargs": {"v": 1}},
    )
    tool_result = await bus.execute(req_tool)
    assert tool_result.status == "success"

    from src.runtime import execution_bus as execution_bus_module

    original_runner = execution_bus_module.task_manager.run_tool_task
    execution_bus_module.task_manager.run_tool_task = _inline_task_runner
    try:
        req_task = make_execution_request(
            source_type="agent",
            execution_type="task",
            session_id="s-injected",
            input_payload={"task_type": "tool_task", "tool_name": "tool_b", "kwargs": {"v": 2}},
        )
        task_result = await bus.execute(req_task)
    finally:
        execution_bus_module.task_manager.run_tool_task = original_runner

    assert task_result.status == "success"
    assert calls == [("tool_a", {"v": 1}), ("tool_b", {"v": 2})]


def test_make_execution_result_defaults():
    result = make_execution_result(request_id="r1", status="success")
    assert result.output_payload == {}
    assert result.artifacts == {}
    assert result.runtime_events == []


def test_make_execution_request_defensive_copies():
    input_payload = {"a": 1}
    metadata = {"m": "v"}
    context_ref = {"c": 2}
    req = make_execution_request(
        source_type="chat",
        execution_type="chat",
        input_payload=input_payload,
        metadata=metadata,
        context_ref=context_ref,
    )
    input_payload["a"] = 99
    metadata["m"] = "changed"
    context_ref["c"] = 77
    assert req.input_payload["a"] == 1
    assert req.metadata["m"] == "v"
    assert req.context_ref["c"] == 2


def test_make_execution_result_defensive_copies_and_explicit_empty():
    output_payload = {"o": 1}
    artifacts = {"k": "v"}
    runtime_events = [{"e": 1}]
    result = make_execution_result(
        request_id="r2",
        status="success",
        output_payload=output_payload,
        artifacts=artifacts,
        runtime_events=runtime_events,
    )
    output_payload["o"] = 9
    artifacts["k"] = "changed"
    runtime_events.append({"e": 2})
    assert result.output_payload["o"] == 1
    assert result.artifacts["k"] == "v"
    assert len(result.runtime_events) == 1

    empty_result = make_execution_result(
        request_id="r3",
        status="success",
        output_payload={},
        artifacts={},
        runtime_events=[],
    )
    assert empty_result.output_payload == {}
    assert empty_result.artifacts == {}
    assert empty_result.runtime_events == []


@pytest.mark.asyncio
async def test_execute_skill_entrypoint_routes_through_bus(monkeypatch):
    from src.agents import executor

    class _FakeBus:
        def __init__(self):
            self.request = None

        async def execute(self, request):
            self.request = request
            return make_execution_result(
                request_id=request.request_id,
                status="success",
                output_payload={"output": "skill-ok", "data": {"x": 1}},
            )

    fake_bus = _FakeBus()
    monkeypatch.setattr("src.runtime.build_default_execution_bus", lambda *args, **kwargs: fake_bus)
    result = await executor.execute_skill("demo_skill", message="hello")

    assert fake_bus.request.execution_type == "skill"
    assert result.success is True
    assert result.output == "skill-ok"


@pytest.mark.asyncio
async def test_execute_skill_none_output_maps_to_empty_string(monkeypatch):
    from src.agents import executor

    class _FakeBus:
        async def execute(self, request):
            return make_execution_result(
                request_id=request.request_id,
                status="success",
                output_payload={"output": None, "data": {}},
            )

    monkeypatch.setattr("src.runtime.build_default_execution_bus", lambda *args, **kwargs: _FakeBus())
    result = await executor.execute_skill("demo_skill", message="hello")
    assert result.output == ""


@pytest.mark.asyncio
async def test_event_forwarding_uses_distinct_request_id_and_parent_link():
    captured = {}

    async def chat_handler(request):
        captured["request_id"] = request.request_id
        captured["metadata"] = request.metadata
        return {"response": "ok"}

    bus = build_default_execution_bus(chat_handler=chat_handler)
    req = make_execution_request(
        source_type="system",
        execution_type="event",
        request_id="parent-req",
        input_payload={"target_execution_type": "chat"},
    )
    result = await bus.execute(req)

    assert captured["request_id"] != "parent-req"
    assert captured["metadata"]["parent_request_id"] == "parent-req"
    assert captured["metadata"]["forwarded_from_execution_type"] == "event"
    assert result.request_id == "parent-req"
    assert result.output_payload["forwarded_request_id"] == captured["request_id"]
    assert result.output_payload["parent_request_id"] == "parent-req"


@pytest.mark.asyncio
async def test_event_handler_invalid_target_falls_back_to_queued():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="system",
        execution_type="event",
        request_id="parent-req",
        input_payload={"target_execution_type": {"bad": "type"}},
    )
    result = await bus.execute(req)
    assert result.status == "queued"
    assert result.output_payload["target_execution_type"] is None


@pytest.mark.asyncio
async def test_subagent_handler_preserves_cleanup(monkeypatch):
    captured = {}

    async def _fake_run_subagent_execution(**kwargs):
        captured.update(kwargs)
        return {"status": "started", "session_key": kwargs["session_key"]}

    monkeypatch.setattr("src.runtime.execution_bus.run_subagent_execution", _fake_run_subagent_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="system",
        execution_type="subagent",
        session_id="s-sub",
        input_payload={"task": "demo", "cleanup": "keep"},
    )
    result = await bus.execute(req)
    assert result.status == "started"
    assert captured["cleanup"] == "keep"


def test_execution_bus_copies_handlers_mapping():
    async def handler(_request):
        return {"response": "ok"}

    provided = {"chat": handler}
    bus = ExecutionBus(handlers=provided)
    provided.clear()
    assert "chat" in bus._handlers
