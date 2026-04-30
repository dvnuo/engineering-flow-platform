import pytest

from src import ToolResult
from src.agents.executor import SkillResult, run_skill_execution
from src.agents.errors import LLMError
from src.runtime.contracts import ExecutionResult, make_execution_request, make_execution_result
from src.runtime.execution_bus import ExecutionBus, build_default_execution_bus
from src.runtime.events import build_runtime_event
from src.runtime.governance import GovernanceHooks
from src.runtime.governance_bus import GovernanceDecision
from src.runtime.task_template_registry import list_task_templates
from src.runtime.governance_bus import GovernanceBus
from src.runtime.capability_registry import CapabilityDescriptor


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
    assert result.output_payload["success"] is True
    assert result.output_payload["content"] == "ok"
    assert result.output_payload["capability_id"] == "tool:run_command"
    assert result.output_payload["capability_type"] == "tool"
    assert result.output_payload["tool_name"] == "run_command"


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
        execution_type="chat",
        state="success",
        session_id="s3",
        request_id="r3",
        agent_id="a3",
        summary="done",
        detail_payload={"k": "v"},
        legacy_payload={"legacy_type": "legacy", "data": {"k": "v"}},
    )

    assert event["detail_payload"] == {"k": "v"}
    assert event["type"] == "chat"
    assert event["legacy_type"] == "legacy"
    assert event["data"] == {"k": "v"}
    assert event["event"] == "runtime.update"
    assert event["execution_id"] == "r3"
    assert event["timestamp"] == event["created_at"]


def test_runtime_event_builder_uses_explicit_execution_type_aliases_when_provided():
    event = build_runtime_event(
        event_type="runtime.update",
        execution_type="chat",
        state="ok",
        session_id="s1",
        request_id="r1",
        agent_id="a1",
        summary="ok",
        detail_payload={"k": "v"},
    )
    assert event["type"] == "chat"
    assert event["execution_type"] == "chat"


def test_runtime_event_builder_allows_legacy_type_when_execution_type_missing():
    event = build_runtime_event(
        event_type="runtime.update",
        execution_type=None,
        state="ok",
        session_id="s1",
        request_id="r1",
        agent_id="a1",
        summary="ok",
        detail_payload={"k": "v"},
        legacy_payload={"type": "legacy-type"},
    )
    assert event["type"] == "legacy-type"


def test_runtime_event_builder_allows_legacy_execution_type_when_missing():
    event = build_runtime_event(
        event_type="runtime.update",
        execution_type=None,
        state="ok",
        session_id="s1",
        request_id="r1",
        agent_id="a1",
        summary="ok",
        detail_payload={"k": "v"},
        legacy_payload={"execution_type": "legacy-execution"},
    )
    assert event["execution_type"] == "legacy-execution"


def test_runtime_event_builder_legacy_payload_does_not_override_alias_collisions():
    event = build_runtime_event(
        event_type="execution.completed",
        execution_type="tool",
        state="success",
        session_id="s1",
        request_id="r1",
        agent_id="a1",
        summary="done",
        detail_payload={"content": "ok"},
        legacy_payload={
            "type": "legacy-type",
            "event": "legacy-event",
            "execution_id": "legacy-exec",
            "payload": {"legacy": True},
            "timestamp": "legacy-ts",
            "legacy_type": "legacy_execution_completed",
        },
    )

    assert event["type"] == "tool"
    assert event["event"] == "execution.completed"
    assert event["execution_id"] == "r1"
    assert event["payload"] == {"content": "ok"}
    assert event["timestamp"] == event["created_at"]
    assert event["legacy_type"] == "legacy_execution_completed"


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


def test_default_governance_hooks_are_noop_for_request_and_result():
    hooks = GovernanceHooks()
    req = make_execution_request(
        source_type="chat",
        execution_type="chat",
        metadata={"existing": "value"},
    )
    result = make_execution_result(
        request_id=req.request_id,
        status="success",
        output_payload={"content": "ok"},
    )
    original_metadata = dict(req.metadata)
    original_output = dict(result.output_payload)

    hooks.before_execute(req)
    hooks.after_execute(req, result)
    hooks.on_error(req, RuntimeError("boom"))

    assert req.metadata == original_metadata
    assert result.output_payload == original_output


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
async def test_execution_bus_async_governance_hooks_are_awaited():
    class _AsyncGovernance(GovernanceHooks):
        def __init__(self):
            self.calls = []

        async def before_execute(self, request):
            self.calls.append(("before", request.request_id))

        async def after_execute(self, request, result):
            self.calls.append(("after", result.status))

    async def _ok(_request):
        return {"response": "ok"}

    governance = _AsyncGovernance()
    bus = ExecutionBus(governance=governance)
    bus.register_handler("chat", _ok)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)

    assert result.status == "success"
    assert governance.calls == [("before", req.request_id), ("after", "success")]


@pytest.mark.asyncio
async def test_execution_bus_async_governance_on_error_is_awaited():
    class _AsyncGovernance(GovernanceHooks):
        def __init__(self):
            self.error_seen = None

        async def on_error(self, request, error):
            self.error_seen = (request.execution_type, error.__class__.__name__)

    async def _bad(_request):
        raise RuntimeError("boom")

    governance = _AsyncGovernance()
    bus = ExecutionBus(governance=governance)
    bus.register_handler("chat", _bad)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)

    assert result.status == "error"
    assert governance.error_seen == ("chat", "RuntimeError")


@pytest.mark.asyncio
async def test_execution_bus_execute_logs_request_context(caplog):
    async def task_handler(_request):
        return {"status": "success", "response": "ok"}

    bus = ExecutionBus(handlers={"task": task_handler})
    req = make_execution_request(
        request_id="req-log-1",
        source_type="task",
        execution_type="task",
        session_id="session-1",
        agent_id="agent-1",
        input_payload={"task_type": "adapter_action_task"},
    )
    with caplog.at_level("INFO"):
        result = await bus.execute(req)
    assert result.status == "success"
    assert "ExecutionBus.execute start" in caplog.text
    assert "request_id=req-log-1" in caplog.text
    assert "execution_type=task" in caplog.text
    assert "task_type=adapter_action_task" in caplog.text


@pytest.mark.asyncio
async def test_execution_bus_logs_blocked_no_handler_and_exception(caplog):
    async def _bad(_request):
        raise RuntimeError("boom")

    class _BlockGovernance(GovernanceBus):
        async def before_execute(self, _request):
            return GovernanceDecision(allowed=False, reason="denied")

        async def after_execute(self, _request, result):
            return result

        async def on_error(self, _request, _error):
            return None

    blocked_bus = ExecutionBus(governance=_BlockGovernance())
    blocked_req = make_execution_request(request_id="req-blocked", source_type="chat", execution_type="chat")

    no_handler_bus = ExecutionBus()
    no_handler_req = make_execution_request(request_id="req-no-handler", source_type="chat", execution_type="missing")

    exception_bus = ExecutionBus(handlers={"chat": _bad})
    exception_req = make_execution_request(
        request_id="req-exception",
        source_type="chat",
        execution_type="chat",
        input_payload={"task_type": "bundle_action_task"},
    )

    with caplog.at_level("WARNING"):
        await blocked_bus.execute(blocked_req)
        await no_handler_bus.execute(no_handler_req)
        await exception_bus.execute(exception_req)

    assert "ExecutionBus governance blocked" in caplog.text
    assert "ExecutionBus missing handler" in caplog.text
    assert "ExecutionBus handler failed" in caplog.text


@pytest.mark.asyncio
async def test_execution_bus_lifecycle_event_includes_trace_id_from_metadata():
    emitted = []

    async def chat_handler(_request):
        return {"response": "ok"}

    bus = ExecutionBus(
        handlers={"chat": chat_handler},
        event_emitter=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    req = make_execution_request(
        request_id="req-trace-1",
        source_type="chat",
        execution_type="chat",
        metadata={"trace_id": "trace-1", "portal_dispatch_id": "dispatch-1"},
    )
    await bus.execute(req)
    assert emitted
    first_payload = emitted[0][1]
    assert first_payload["detail_payload"]["trace_id"] == "trace-1"
    assert first_payload["detail_payload"]["portal_dispatch_id"] == "dispatch-1"


@pytest.mark.asyncio
async def test_execution_bus_async_governance_exceptions_are_swallowed(caplog):
    class _FailingAsyncGovernance(GovernanceHooks):
        async def before_execute(self, request):
            raise RuntimeError("before failed")

        async def after_execute(self, request, result):
            raise RuntimeError("after failed")

        async def on_error(self, request, error):
            raise RuntimeError("on_error failed")

    async def _bad(_request):
        raise RuntimeError("handler boom")

    with caplog.at_level("DEBUG"):
        bus = ExecutionBus(governance=_FailingAsyncGovernance())
        bus.register_handler("chat", _bad)
        req = make_execution_request(source_type="chat", execution_type="chat")
        result = await bus.execute(req)

    assert result.status == "error"
    assert "ExecutionBus governance hook failed: before_execute" in caplog.text
    assert "ExecutionBus governance hook failed: on_error" in caplog.text
    assert "ExecutionBus governance hook failed: after_execute" in caplog.text


@pytest.mark.asyncio
async def test_execution_bus_emits_terminal_event_from_final_governed_result():
    emitted = []

    class _BlockingAfterGovernance(GovernanceHooks):
        def after_execute(self, request, result):
            result.status = "blocked"
            result.output_payload["status"] = "blocked-by-governance"
            result.audit_ref = "audit-final"
            result.runtime_events.append({"event_type": "governance.enriched"})
            return GovernanceDecision(allowed=False, reason="post_policy_block", result=result)

    async def _ok(_request):
        return {"response": "ok"}

    bus = ExecutionBus(
        governance=_BlockingAfterGovernance(),
        event_emitter=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    bus.register_handler("chat", _ok)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)

    assert result.status == "blocked"
    assert result.audit_ref == "audit-final"
    assert any(evt.get("event_type") == "governance.enriched" for evt in result.runtime_events)
    assert [name for name, _ in emitted] == ["execution_started", "execution_failed"]
    assert emitted[-1][1]["detail_payload"]["status"] == "blocked"
    assert emitted[-1][1]["detail_payload"]["output_summary"]["status"] == "blocked-by-governance"


@pytest.mark.asyncio
async def test_execution_bus_error_lifecycle_uses_post_governance_final_status():
    emitted = []

    class _ErrorToBlockedGovernance(GovernanceHooks):
        def after_execute(self, request, result):
            if result.status == "error":
                result.status = "blocked"
                result.output_payload["status"] = "blocked-after-error"
            return result

    async def _bad(_request):
        raise RuntimeError("boom")

    bus = ExecutionBus(
        governance=_ErrorToBlockedGovernance(),
        event_emitter=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    bus.register_handler("chat", _bad)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)

    assert result.status == "blocked"
    assert [name for name, _ in emitted] == ["execution_started", "execution_failed"]
    assert emitted[-1][1]["detail_payload"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_tool_task(monkeypatch):
    captured = {}

    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        captured["session_id"] = session_id
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
    assert result.output_payload["capability_id"] == "tool:demo_tool"
    assert result.output_payload["capability_type"] == "tool"
    assert captured["session_id"] == "s-task"


@pytest.mark.asyncio
async def test_execution_bus_tool_handler_failure_preserves_normalized_capability_metadata(monkeypatch):
    async def _fake_execute_tool_by_name(_name, **_kwargs):
        return ToolResult(success=False, content=None, error="tool failed")

    monkeypatch.setattr("src.runtime.execution_bus.execute_tool_by_name", _fake_execute_tool_by_name)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="system",
        execution_type="tool",
        input_payload={"tool_name": "run_command", "kwargs": {"cmd": "false"}},
    )

    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "tool failed"
    assert result.output_payload["capability_id"] == "tool:run_command"
    assert result.output_payload["capability_type"] == "tool"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_accepts_toolresult_like_object(monkeypatch):
    class _ToolResultLike:
        def __init__(self):
            self.success = True
            self.content = "like-ok"
            self.error = None

    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return _ToolResultLike()

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["content"] == "like-ok"
    assert result.output_payload["result"]["success"] is True


@pytest.mark.asyncio
async def test_execution_bus_task_handler_accepts_dict_result(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return {"success": True, "output": "dict-ok", "meta": "x"}

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["content"] == "dict-ok"
    assert result.output_payload["result"]["meta"] == "x"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_dict_status_error_maps_to_failure(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return {"status": "error", "content": "failed"}

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["success"] is False
    assert result.output_payload["error"] is not None
    assert result.output_payload["capability_id"] == "tool:demo_tool"
    assert result.output_payload["capability_type"] == "tool"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_dict_status_blocked_maps_to_failure(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return {"status": "blocked", "content": "blocked"}

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["success"] is False
    assert result.output_payload["error"] is not None


@pytest.mark.asyncio
async def test_execution_bus_task_handler_dict_status_success_maps_to_success(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return {"status": "success", "content": "ok"}

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["success"] is True


@pytest.mark.asyncio
async def test_execution_bus_task_handler_dict_explicit_success_overrides_status(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return {"success": True, "status": "error", "content": "ok"}

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["success"] is True


@pytest.mark.asyncio
async def test_execution_bus_task_handler_status_error_uses_content_fallback_for_error(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return {"status": "error", "content": "permission denied"}

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "permission denied"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_explicit_error_wins_over_fallback(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return {"status": "error", "content": "permission denied", "error": "explicit failure"}

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "explicit failure"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_dict_without_status_keeps_existing_inference(monkeypatch):
    async def _error_only(*, session_id, tool_name, coro_factory, event_callback=None):
        return {"error": "boom"}

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _error_only)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["success"] is False

    async def _content_only(*, session_id, tool_name, coro_factory, event_callback=None):
        return {"content": "ok"}

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _content_only)
    result2 = await bus.execute(req)
    assert result2.status == "success"
    assert result2.output_payload["success"] is True


@pytest.mark.asyncio
async def test_execution_bus_task_handler_accepts_string_result(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return "string-ok"

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["content"] == "string-ok"
    assert result.output_payload["result"]["value"] == "string-ok"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_adapter_action_task_success(monkeypatch):
    class _Registry:
        @staticmethod
        def get(action_id):
            return CapabilityDescriptor(
                capability_id=action_id,
                type="adapter_action",
                name="read_issue",
                requires_identity_binding=True,
                policy_tags=["jira", "read"],
            )

    async def _fake_execute_adapter_action(action_id, kwargs):
        return {
            "success": True,
            "error": None,
            "result": {"issue": "ok"},
            "runtime_events": [{"event_type": "task.adapter_action.completed"}],
        }

    monkeypatch.setattr("src.runtime.execution_bus.get_capability_registry", lambda: _Registry())
    monkeypatch.setattr("src.runtime.execution_bus.execute_adapter_action", _fake_execute_adapter_action)

    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={
            "task_type": "adapter_action_task",
            "action_id": "adapter:jira:read_issue",
            "kwargs": {"issue_key": "PROJ-1"},
        },
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["task_type"] == "adapter_action_task"
    assert result.output_payload["action_id"] == "adapter:jira:read_issue"
    assert result.output_payload["requires_identity_binding"] is True


@pytest.mark.asyncio
async def test_execution_bus_task_handler_adapter_action_add_comment(monkeypatch):
    class _Registry:
        @staticmethod
        def get(action_id):
            return CapabilityDescriptor(
                capability_id=action_id,
                type="adapter_action",
                name="add_comment",
                requires_identity_binding=True,
                policy_tags=["jira", "write", "comment"],
            )

    async def _fake_execute_adapter_action(action_id, kwargs):
        return {
            "success": True,
            "error": None,
            "result": {"message": "ok"},
            "runtime_events": [{"event_type": "task.adapter_action.completed"}],
        }

    monkeypatch.setattr("src.runtime.execution_bus.get_capability_registry", lambda: _Registry())
    monkeypatch.setattr("src.runtime.execution_bus.execute_adapter_action", _fake_execute_adapter_action)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={
            "task_type": "adapter_action_task",
            "action_id": "adapter:jira:add_comment",
            "kwargs": {"issue_key": "PROJ-7", "comment": "done"},
        },
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["action_id"] == "adapter:jira:add_comment"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_adapter_action_unknown_is_blocked(monkeypatch):
    class _Registry:
        @staticmethod
        def get(_action_id):
            return None

    monkeypatch.setattr("src.runtime.execution_bus.get_capability_registry", lambda: _Registry())

    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "adapter_action_task", "action_id": "adapter:jira:missing", "kwargs": {}},
    )
    result = await bus.execute(req)

    assert result.status == "blocked"
    assert "Unknown or non-adapter action_id" in result.output_payload["error"]


@pytest.mark.asyncio
async def test_execution_bus_task_handler_adapter_action_github_success(monkeypatch):
    class _Registry:
        @staticmethod
        def get(action_id):
            return CapabilityDescriptor(
                capability_id=action_id,
                type="adapter_action",
                name="review_pull_request",
                requires_identity_binding=True,
                policy_tags=["github", "review"],
            )

    async def _fake_execute_adapter_action(action_id, kwargs):
        return {
            "success": True,
            "error": None,
            "result": {"summary": "review summary"},
            "runtime_events": [{"event_type": "task.adapter_action.completed"}],
        }

    monkeypatch.setattr("src.runtime.execution_bus.get_capability_registry", lambda: _Registry())
    monkeypatch.setattr("src.runtime.execution_bus.execute_adapter_action", _fake_execute_adapter_action)

    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={
            "task_type": "adapter_action_task",
            "action_id": "adapter:github:review_pull_request",
            "kwargs": {"owner": "acme", "repo": "demo", "pull_number": 10},
        },
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["action_id"] == "adapter:github:review_pull_request"
    assert result.output_payload["success"] is True


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_success(monkeypatch):
    events = []
    captured = {}

    async def _fake_run_skill_execution(skill_name, **kwargs):
        captured["skill_name"] = skill_name
        captured["kwargs"] = kwargs
        return {"success": True, "output": f"done:{skill_name}", "data": {"kwargs": kwargs}}

    class _SessionManager:
        def __init__(self):
            self.added = []
            self.completed = []

        async def add_pending_delegation(self, session_id, delegation_record):
            self.added.append((session_id, delegation_record))

        async def complete_pending_delegation(self, session_id, delegation_id, *, status):
            self.completed.append((session_id, delegation_id, status))

    sm = _SessionManager()
    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    monkeypatch.setattr("src.sessions.manager.session_manager", sm)
    bus = build_default_execution_bus(event_emitter=lambda event_type, payload: events.append((event_type, payload)))
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        metadata={"trace_id": "t-1"},
        context_ref={"workspace": "w1"},
        input_payload={
            "task_id": "task-del-1",
            "task_type": "delegation_task",
            "delegation_id": "del-1",
            "objective": "Review",
            "visibility": "leader_only",
            "leader_agent_id": "leader-1",
            "shared_context_ref": "shared-ctx-1",
            "scoped_context_ref": "scope-1",
            "skill_name": "demo_skill",
            "skill_kwargs": {"x": 1},
        },
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["task_type"] == "delegation_task"
    assert result.output_payload["delegation_id"] == "del-1"
    assert result.output_payload["success"] is True
    assert result.output_payload["task_boundary"] is True
    assert result.output_payload["delegation_result"]["status"] == "completed"
    assert set(result.output_payload["delegation_result"].keys()) == {
        "delegation_id",
        "assignee_agent_id",
        "status",
        "summary",
        "artifacts",
        "blockers",
        "next_recommendation",
        "audit_trace",
        "raw_result",
    }
    assert "result_summary" not in result.output_payload["delegation_result"]
    assert "result_artifacts_json" not in result.output_payload["delegation_result"]
    assert captured["kwargs"]["delegation_context"]["delegation_id"] == "del-1"
    assert captured["kwargs"]["delegation_context"]["objective"] == "Review"
    assert captured["kwargs"]["delegation_context"]["visibility"] == "leader_only"
    assert captured["kwargs"]["delegation_context"]["leader_agent_id"] == "leader-1"
    assert captured["kwargs"]["delegation_context"]["shared_context_ref"] == "shared-ctx-1"
    assert captured["kwargs"]["delegation_context"]["scoped_context_ref"] == "scope-1"
    assert captured["kwargs"]["delegation_context"]["context_ref"] == {"workspace": "w1"}
    assert captured["kwargs"]["delegation_context"]["shared_context_materialized"] is True
    assert captured["kwargs"]["delegation_context"]["request_metadata"] == {"trace_id": "t-1"}
    assert captured["kwargs"]["session_id"] == "s-del"
    assert sm.added and sm.added[0][1]["delegation_id"] == "del-1"
    assert sm.added[0][1]["leader_agent_id"] == "leader-1"
    assert sm.completed == [("s-del", "del-1", "completed")]
    delegation_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.completed")
    assert delegation_event["task_id"] == "task-del-1"
    assert delegation_event["detail_payload"]["leader_agent_id"] == "leader-1"
    assert delegation_event["detail_payload"]["shared_context_materialized"] is True
    assert result.output_payload["delegation_result"]["audit_trace"]["leader_agent_id"] == "leader-1"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_uses_nested_structured_result(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {
            "success": True,
            "delegation_result": {
                "summary": "nested-summary",
                "artifacts": [{"artifact_id": "a1"}],
                "blockers": ["none"],
                "next_recommendation": "continue",
                "audit_trace": {"from_skill": True},
            },
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-nested",
            "objective": "Review",
            "visibility": "leader_only",
            "leader_agent_id": "leader-nested",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    payload = result.output_payload["delegation_result"]
    assert payload["summary"] == "nested-summary"
    assert payload["artifacts"] == [{"artifact_id": "a1"}]
    assert payload["blockers"] == ["none"]
    assert payload["next_recommendation"] == "continue"
    assert payload["audit_trace"]["from_skill"] is True
    assert payload["audit_trace"]["leader_agent_id"] == "leader-nested"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_top_level_structured_fallback(monkeypatch):
    captured = {}

    async def _fake_run_skill_execution(_skill_name, **kwargs):
        captured["kwargs"] = kwargs
        return {
            "success": False,
            "summary": "top-level-summary",
            "artifacts": [{"artifact_id": "a2"}],
            "blockers": ["needs_data"],
            "next_recommendation": "retry_later",
            "audit_trace": {"top_level": True},
            "error": "skill failed",
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-top",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    payload = result.output_payload["delegation_result"]
    assert payload["summary"] == "top-level-summary"
    assert payload["artifacts"] == [{"artifact_id": "a2"}]
    assert payload["blockers"] == ["needs_data"]
    assert payload["next_recommendation"] == "retry_later"
    assert payload["audit_trace"]["top_level"] is True
    assert captured["kwargs"]["delegation_context"]["shared_context_materialized"] is False
    assert captured["kwargs"]["delegation_context"]["context_ref"] == {}
    delegation_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.failed")
    assert delegation_event["detail_payload"]["shared_context_materialized"] is False


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_missing_skill_name_blocked():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_id": "task-del-2",
            "task_type": "delegation_task",
            "delegation_id": "del-2",
            "objective": "Review",
            "visibility": "group_visible",
        },
    )
    result = await bus.execute(req)

    assert result.status == "blocked"
    assert result.output_payload["success"] is False
    assert result.output_payload["task_boundary"] is True
    assert result.output_payload["delegation_result"]["status"] == "blocked"
    failed_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.failed")
    assert failed_event["task_id"] == "task-del-2"
    assert "leader_agent_id" in failed_event["detail_payload"]


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_invalid_skill_kwargs_type_blocked():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_id": "task-del-kw",
            "task_type": "delegation_task",
            "delegation_id": "del-kw",
            "objective": "Review",
            "visibility": "group_visible",
            "skill_name": "demo_skill",
            "skill_kwargs": "not-a-dict",
        },
    )
    result = await bus.execute(req)
    assert result.status == "blocked"
    assert result.output_payload["success"] is False
    assert result.output_payload["delegation_result"]["status"] == "blocked"
    failed_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.failed")
    assert failed_event["detail_payload"]["delegation_id"] == "del-kw"
    assert failed_event["task_id"] == "task-del-kw"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_invalid_visibility_emits_failed_event():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_id": "task-del-vis",
            "task_type": "delegation_task",
            "delegation_id": "del-vis",
            "objective": "Review",
            "visibility": "public",
            "leader_agent_id": "leader-vis",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    assert result.status == "blocked"
    assert result.output_payload["success"] is False
    assert result.output_payload["delegation_result"]["status"] == "blocked"
    failed_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.failed")
    assert failed_event["detail_payload"]["visibility"] == "public"
    assert failed_event["detail_payload"]["leader_agent_id"] == "leader-vis"
    assert failed_event["detail_payload"]["shared_context_materialized"] is False
    assert result.output_payload["delegation_result"]["audit_trace"]["leader_agent_id"] == "leader-vis"
    assert failed_event["task_id"] == "task-del-vis"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_resolves_shared_context_ref_from_metadata(monkeypatch):
    captured = {}

    async def _fake_run_skill_execution(skill_name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True, "output": "ok"}

    class _SessionManager:
        def __init__(self):
            self.added = []

        async def add_pending_delegation(self, session_id, delegation_record):
            self.added.append(delegation_record)

        async def complete_pending_delegation(self, session_id, delegation_id, *, status):
            return None

    sm = _SessionManager()
    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    monkeypatch.setattr("src.sessions.manager.session_manager", sm)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        metadata={"shared_context_ref": "ctx://from-metadata"},
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-meta-ref",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert captured["kwargs"]["delegation_context"]["shared_context_ref"] == "ctx://from-metadata"
    assert sm.added[0]["shared_context_ref"] == "ctx://from-metadata"
    delegation_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.completed")
    assert delegation_event["detail_payload"]["shared_context_ref"] == "ctx://from-metadata"


@pytest.mark.asyncio
async def test_execute_runtime_task_request_forwards_context_ref_to_execution_request(monkeypatch):
    from src.runtime import chat_orchestration_adapter as adapter

    captured = {}

    class _FakeBus:
        async def execute(self, request):
            captured["context_ref"] = request.context_ref
            captured["agent_id"] = request.agent_id
            return make_execution_result(request_id=request.request_id, status="success", output_payload={"ok": True})

    monkeypatch.setattr(adapter, "build_default_execution_bus", lambda **kwargs: _FakeBus())
    result = await adapter.execute_runtime_task_request(
        request_id="task-ctx-1",
        source_type="task",
        source_ref="portal",
        execution_type="task",
        session_id="s-1",
        agent_id="agent-task-1",
        context_ref={"workspace": "w1"},
        input_payload={"task_type": "delegation_task", "delegation_id": "d1", "objective": "x", "visibility": "leader_only", "skill_name": "demo"},
        metadata={},
    )
    assert result.status == "success"
    assert captured["context_ref"] == {"workspace": "w1"}
    assert captured["agent_id"] == "agent-task-1"


@pytest.mark.asyncio
async def test_execute_chat_orchestration_forwards_agent_id(monkeypatch):
    from src.runtime import chat_orchestration_adapter as adapter

    captured = {}

    class _FakeBus:
        def register_handler(self, *_args, **_kwargs):
            return None

        async def execute(self, request):
            captured["agent_id"] = request.agent_id
            return make_execution_result(request_id=request.request_id, status="success", output_payload={"response": "ok"})

    monkeypatch.setattr(adapter, "build_default_execution_bus", lambda **kwargs: _FakeBus())
    result = await adapter.execute_chat_orchestration(
        request_id="chat-1",
        session_id="s-1",
        source_ref="test",
        input_payload={"message": "hi"},
        metadata={},
        chat_handler=lambda _request: {"response": "ok"},
        agent_id="agent-chat-1",
    )
    assert result.status == "success"
    assert captured["agent_id"] == "agent-chat-1"


@pytest.mark.asyncio
async def test_execution_bus_delegation_execution_type_works_without_task_handler_wrapping(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {
            "success": True,
            "delegation_result": {
                "summary": "delegation-direct",
                "artifacts": [],
                "blockers": [],
                "audit_trace": {"from_skill": True},
                "status": "completed",
            },
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="delegation",
        session_id="s-del-direct",
        input_payload={
            "delegation_id": "del-direct-1",
            "objective": "Review",
            "visibility": "leader_only",
            "leader_agent_id": "leader-1",
            "group_id": "group-1",
            "parent_agent_id": "parent-1",
            "assignee_agent_id": "assignee-1",
            "skill_name": "demo_skill",
            "strict_delegation_result": True,
            "shared_context_ref": "ctx://1",
            "scoped_context_ref": "scope://1",
            "agent_mode": "task",
            "ephemeral_task_agent_id": "task-agent-1",
            "task_agent_template_id": "tmpl-1",
            "task_agent_scope": "repo:acme/demo",
            "task_agent_cleanup_policy": "delete_after_completion",
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["task_type"] == "delegation"
    assert result.output_payload["delegation_id"] == "del-direct-1"
    delegation_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.completed")
    assert delegation_event["execution_type"] == "delegation"
    detail = delegation_event["detail_payload"]
    for key in [
        "delegation_id",
        "group_id",
        "leader_agent_id",
        "parent_agent_id",
        "assignee_agent_id",
        "visibility",
        "skill_name",
        "shared_context_ref",
        "scoped_context_ref",
        "shared_context_materialized",
        "leader_session_id",
        "strict_delegation_result",
        "agent_mode",
        "ephemeral_task_agent_id",
        "task_agent_template_id",
        "task_agent_scope",
        "task_agent_cleanup_policy",
    ]:
        assert key in detail


@pytest.mark.asyncio
async def test_execution_bus_coordination_delegation_batch_calls_helper_and_emits_summary_event(monkeypatch):
    async def _fake_dispatch(**kwargs):
        assert kwargs["group_id"] == "group-1"
        return {
            "success": False,
            "created": 1,
            "failed": 1,
            "items": [
                {"result": {"delegation_id": "d-1", "success": True}},
                {"result": {"delegation_id": None, "success": False}},
            ],
        }

    monkeypatch.setattr("src.runtime.execution_bus.dispatch_task_breakdown_as_delegations", _fake_dispatch)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="coordination",
        session_id="s-coord",
        input_payload={
            "coordination_type": "delegation_batch",
            "group_id": "group-1",
            "leader_agent_id": "leader-1",
            "leader_session_id": "leader-session-1",
            "tasks": [{"assignee_agent_id": "a-1", "objective": "x"}],
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["coordination_type"] == "delegation_batch"
    summary_event = next(evt for evt in result.runtime_events if evt.get("event_type").startswith("coordination.delegation_batch"))
    assert summary_event["detail_payload"]["group_id"] == "group-1"
    assert summary_event["detail_payload"]["leader_agent_id"] == "leader-1"
    assert summary_event["detail_payload"]["leader_session_id"] == "leader-session-1"
    assert summary_event["detail_payload"]["created_count"] == 1
    assert summary_event["detail_payload"]["failed_count"] == 1


@pytest.mark.asyncio
async def test_execution_bus_coordination_missing_fields_returns_error():
    bus = build_default_execution_bus()
    req = make_execution_request(source_type="agent", execution_type="coordination", input_payload={"coordination_type": "delegation_batch"})
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["coordination_type"] == "delegation_batch"


@pytest.mark.asyncio
async def test_execution_bus_coordination_delegation_cycle_emits_cycle_event(monkeypatch):
    async def _fake_run_cycle(**kwargs):
        assert kwargs["group_id"] == "group-1"
        return {
            "success": True,
            "coordination_run_id": "coord-run-1",
            "round_index": 1,
            "created": 2,
            "failed": 0,
            "items": [{"result": {"delegation_id": "d-1"}}, {"result": {"delegation_id": "d-2"}}],
            "aggregate": {"all_done": False},
            "run_state": {
                "status": "running",
                "completed_at": None,
                "summary": {"hint": "awaiting specialists"},
                "latest_round_index": 3,
                "status_counts": {"queued": 0, "running": 1, "done": 1, "failed": 0},
                "all_terminal": False,
            },
            "is_complete": False,
            "next_action": "continue",
            "leader_summary": {"status": "in_progress", "run_status": "running"},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_delegation_cycle", _fake_run_cycle)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="coordination",
        session_id="s-cycle",
        input_payload={
            "coordination_type": "delegation_cycle",
            "group_id": "group-1",
            "leader_agent_id": "leader-1",
            "leader_session_id": "leader-session-1",
            "round_index": 1,
            "tasks": [{"assignee_agent_id": "a-1", "objective": "Task"}],
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["coordination_run_id"] == "coord-run-1"
    assert result.output_payload["run_state"]["status"] == "running"
    assert result.output_payload["leader_summary"]["run_status"] == "running"
    event = next(evt for evt in result.runtime_events if evt.get("event_type").startswith("coordination.delegation_cycle"))
    assert event["detail_payload"]["coordination_run_id"] == "coord-run-1"
    assert event["detail_payload"]["round_index"] == 1
    assert event["detail_payload"]["next_action"] == "continue"
    assert event["detail_payload"]["run_status"] == "running"
    assert event["detail_payload"]["summary"]["hint"] == "awaiting specialists"
    assert event["detail_payload"]["latest_round_index"] == 3
    assert event["detail_payload"]["status_counts"]["running"] == 1


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_marks_failed_completion(monkeypatch):
    async def _fake_run_skill_execution(skill_name, **kwargs):
        return {"success": False, "error": "skill failed"}

    class _SessionManager:
        def __init__(self):
            self.completed = []

        async def add_pending_delegation(self, session_id, delegation_record):
            return None

        async def complete_pending_delegation(self, session_id, delegation_id, *, status):
            self.completed.append((session_id, delegation_id, status))

    sm = _SessionManager()
    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    monkeypatch.setattr("src.sessions.manager.session_manager", sm)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-3",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert sm.completed == [("s-del", "del-3", "failed")]


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_invalid_normalized_payload_returns_error(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {"success": True, "output": "ok"}

    def _fake_build_payload(**_kwargs):
        return {
            "summary": 123,
            "artifacts": [{"artifact_id": "a1"}],
            "blockers": [],
            "next_recommendation": None,
            "audit_trace": {},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    monkeypatch.setattr("src.runtime.execution_bus._build_structured_delegation_payload_from_skill_output", _fake_build_payload)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_id": "task-del-invalid-payload",
            "task_type": "delegation_task",
            "delegation_id": "del-invalid-payload",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "invalid_delegation_result"
    assert result.output_payload["success"] is False
    assert "delegation_result" in result.output_payload
    assert "invalid_delegation_result" in result.output_payload["delegation_result"]["blockers"]
    failed_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.failed")
    assert failed_event["task_id"] == "task-del-invalid-payload"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_expected_output_schema_required_fields(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {
            "success": True,
            "delegation_result": {
                "summary": "done",
                "artifacts": [{"artifact_id": "a1"}],
                "blockers": [],
                "next_recommendation": None,
                "audit_trace": {},
            },
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_id": "task-del-schema-required",
            "task_type": "delegation_task",
            "delegation_id": "del-schema-required",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "expected_output_schema": {
                "required": ["summary", "nonexistent_field"],
            },
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "expected_output_schema_validation_failed"
    assert "expected_output_schema_validation_failed" in result.output_payload["delegation_result"]["blockers"]
    failed_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.failed")
    assert failed_event["task_id"] == "task-del-schema-required"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_expected_output_schema_property_types(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {
            "success": True,
            "delegation_result": {
                "summary": "done",
                "artifacts": [{"artifact_id": "a1"}],
                "blockers": [],
                "next_recommendation": None,
                "audit_trace": {},
            },
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_id": "task-del-schema-types",
            "task_type": "delegation_task",
            "delegation_id": "del-schema-types",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "expected_output_schema": {
                "properties": {
                    "summary": {"type": "array"},
                    "artifacts": {"type": "array"},
                },
            },
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "expected_output_schema_validation_failed"
    assert "expected_output_schema_validation_failed" in result.output_payload["delegation_result"]["blockers"]


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_propagates_leader_session_id_to_events_and_audit(monkeypatch):
    captured = {}

    async def _fake_run_skill_execution(_skill_name, **kwargs):
        captured["delegation_context"] = kwargs.get("delegation_context")
        return {
            "success": True,
            "delegation_result": {
                "summary": "done",
                "artifacts": [{"artifact_id": "a1"}],
                "blockers": [],
                "next_recommendation": None,
                "audit_trace": {"source": "skill"},
            },
        }

    class _SessionManager:
        def __init__(self):
            self.added = []

        async def add_pending_delegation(self, session_id, delegation_record):
            self.added.append((session_id, delegation_record))

        async def complete_pending_delegation(self, session_id, delegation_id, *, status):
            return None

    sm = _SessionManager()
    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    monkeypatch.setattr("src.sessions.manager.session_manager", sm)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="fallback-session",
        metadata={"portal_leader_session_id": "leader-session-1"},
        input_payload={
            "task_id": "task-del-leader-session",
            "task_type": "delegation_task",
            "delegation_id": "del-leader-session",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert captured["delegation_context"]["leader_session_id"] == "leader-session-1"
    assert sm.added[0][1]["leader_session_id"] == "leader-session-1"
    assert result.output_payload["delegation_result"]["audit_trace"]["leader_session_id"] == "leader-session-1"
    delegation_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.completed")
    assert delegation_event["detail_payload"]["leader_session_id"] == "leader-session-1"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_strict_mode_rejects_top_level_fallback(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {"success": True, "output": "done"}

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_id": "task-del-strict-fallback",
            "task_type": "delegation_task",
            "delegation_id": "del-strict-fallback",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "strict_delegation_result": True,
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "invalid_delegation_result"
    assert "invalid_delegation_result" in result.output_payload["delegation_result"]["blockers"]


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_strict_mode_accepts_nested_result(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {
            "success": True,
            "delegation_result": {
                "summary": "strict-ok",
                "artifacts": [{"artifact_id": "a1"}],
                "blockers": [],
                "next_recommendation": "continue",
                "audit_trace": {"from_skill": True},
                "status": "completed",
            },
            "output": "should-not-be-used",
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        metadata={"strict_delegation_result": True},
        input_payload={
            "task_id": "task-del-strict-ok",
            "task_type": "delegation_task",
            "delegation_id": "del-strict-ok",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    payload = result.output_payload["delegation_result"]
    assert payload["summary"] == "strict-ok"
    assert payload["status"] == "completed"
    assert payload["audit_trace"]["strict_delegation_result"] is True
    delegation_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.completed")
    assert delegation_event["detail_payload"]["strict_delegation_result"] is True


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_non_strict_keeps_top_level_fallback(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {"success": True, "output": "fallback-done"}

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-nonstrict-fallback",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["delegation_result"]["summary"] == "fallback-done"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_strict_mode_still_validates_expected_output_schema(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {
            "success": True,
            "delegation_result": {
                "summary": "strict-summary",
                "artifacts": [{"artifact_id": "a1"}],
                "blockers": [],
                "next_recommendation": None,
                "audit_trace": {},
                "status": "completed",
            },
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_id": "task-del-strict-schema",
            "task_type": "delegation_task",
            "delegation_id": "del-strict-schema",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "strict_delegation_result": True,
            "expected_output_schema": {"properties": {"summary": {"type": "array"}}},
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "expected_output_schema_validation_failed"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_strict_failure_event_contains_marker(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {"success": True, "output": "done"}

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-del",
        input_payload={
            "task_id": "task-del-strict-fail-event",
            "task_type": "delegation_task",
            "delegation_id": "del-strict-fail-event",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "strict_delegation_result": True,
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    failed_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.failed")
    assert failed_event["detail_payload"]["strict_delegation_result"] is True
    assert "validation_errors" in failed_event["detail_payload"]


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_agent_mode_task_requires_leader_session(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {"success": True, "delegation_result": {"summary": "done", "artifacts": [], "blockers": [], "audit_trace": {}, "status": "completed"}}

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id=None,
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-task-missing-leader-session",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "agent_mode": "task",
            "strict_delegation_result": True,
            "ephemeral_task_agent_id": "task-agent-1",
            "task_agent_scope": "repo:acme/demo",
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "invalid_task_agent_context"
    assert result.output_payload["task_boundary"] is True
    assert "delegation_result" in result.output_payload


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_agent_mode_task_requires_ephemeral_agent_id(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {"success": True, "delegation_result": {"summary": "done", "artifacts": [], "blockers": [], "audit_trace": {}, "status": "completed"}}

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="leader-session-1",
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-task-missing-agent-id",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "agent_mode": "task",
            "strict_delegation_result": True,
            "task_agent_scope": "repo:acme/demo",
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "invalid_task_agent_context"
    assert result.output_payload["task_boundary"] is True
    assert "delegation_result" in result.output_payload


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_agent_mode_task_requires_scope(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {"success": True, "delegation_result": {"summary": "done", "artifacts": [], "blockers": [], "audit_trace": {}, "status": "completed"}}

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="leader-session-1",
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-task-missing-scope",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "agent_mode": "task",
            "strict_delegation_result": True,
            "ephemeral_task_agent_id": "task-agent-1",
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "invalid_task_agent_context"
    assert result.output_payload["task_boundary"] is True
    assert "delegation_result" in result.output_payload


@pytest.mark.asyncio
async def test_execution_bus_task_handler_delegation_task_agent_mode_task_requires_strict_mode(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        return {"success": True, "delegation_result": {"summary": "done", "artifacts": [], "blockers": [], "audit_trace": {}, "status": "completed"}}

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="leader-session-1",
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-task-non-strict",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "agent_mode": "task",
            "strict_delegation_result": False,
            "ephemeral_task_agent_id": "task-agent-1",
            "task_agent_scope": "repo:acme/demo",
        },
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error"] == "invalid_task_agent_context"
    assert result.output_payload["task_boundary"] is True
    assert "delegation_result" in result.output_payload


@pytest.mark.asyncio
async def test_execution_bus_task_handler_valid_task_agent_context_propagates_metadata(monkeypatch):
    captured = {}

    async def _fake_run_skill_execution(_skill_name, **kwargs):
        captured["delegation_context"] = kwargs.get("delegation_context")
        return {
            "success": True,
            "delegation_result": {
                "summary": "task-agent-done",
                "artifacts": [{"artifact_id": "a1"}],
                "blockers": [],
                "next_recommendation": "continue",
                "audit_trace": {"from_skill": True},
                "status": "completed",
            },
        }

    class _SessionManager:
        def __init__(self):
            self.added = []

        async def add_pending_delegation(self, session_id, delegation_record):
            self.added.append((session_id, delegation_record))

        async def complete_pending_delegation(self, session_id, delegation_id, *, status):
            return None

    sm = _SessionManager()
    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    monkeypatch.setattr("src.sessions.manager.session_manager", sm)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="leader-session-2",
        input_payload={
            "task_id": "task-del-task-agent-valid",
            "task_type": "delegation_task",
            "delegation_id": "del-task-agent-valid",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
            "agent_mode": "task",
            "strict_delegation_result": True,
            "ephemeral_task_agent_id": "task-agent-9",
            "task_agent_template_id": "template-1",
            "task_agent_scope": "repo:acme/demo",
            "task_agent_cleanup_policy": "delete_after_completion",
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert captured["delegation_context"]["agent_mode"] == "task"
    assert captured["delegation_context"]["ephemeral_task_agent_id"] == "task-agent-9"
    assert captured["delegation_context"]["task_agent_template_id"] == "template-1"
    assert captured["delegation_context"]["task_agent_scope"] == "repo:acme/demo"
    assert captured["delegation_context"]["task_agent_cleanup_policy"] == "delete_after_completion"
    assert sm.added[0][1]["agent_mode"] == "task"
    assert sm.added[0][1]["ephemeral_task_agent_id"] == "task-agent-9"
    assert sm.added[0][1]["task_agent_template_id"] == "template-1"
    assert sm.added[0][1]["task_agent_scope"] == "repo:acme/demo"
    assert sm.added[0][1]["task_agent_cleanup_policy"] == "delete_after_completion"
    audit_trace = result.output_payload["delegation_result"]["audit_trace"]
    assert audit_trace["agent_mode"] == "task"
    assert audit_trace["ephemeral_task_agent_id"] == "task-agent-9"
    assert audit_trace["task_agent_template_id"] == "template-1"
    assert audit_trace["task_agent_scope"] == "repo:acme/demo"
    assert audit_trace["task_agent_cleanup_policy"] == "delete_after_completion"
    assert audit_trace["leader_session_id"] == "leader-session-2"
    assert audit_trace["strict_delegation_result"] is True
    delegation_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.completed")
    assert delegation_event["detail_payload"]["agent_mode"] == "task"
    assert delegation_event["detail_payload"]["ephemeral_task_agent_id"] == "task-agent-9"
    assert delegation_event["detail_payload"]["task_agent_template_id"] == "template-1"
    assert delegation_event["detail_payload"]["task_agent_scope"] == "repo:acme/demo"
    assert delegation_event["detail_payload"]["task_agent_cleanup_policy"] == "delete_after_completion"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_specialist_mode_remains_backward_compatible(monkeypatch):
    async def _fake_run_skill_execution(_skill_name, **kwargs):
        return {
            "success": True,
            "delegation_result": {
                "summary": "specialist-ok",
                "artifacts": [],
                "blockers": [],
                "audit_trace": {"from_skill": True},
                "status": "completed",
            },
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="leader-session-specialist",
        input_payload={
            "task_type": "delegation_task",
            "delegation_id": "del-specialist-mode",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "demo_skill",
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    delegation_event = next(evt for evt in result.runtime_events if evt.get("event_type") == "task.delegation.completed")
    assert delegation_event["detail_payload"]["agent_mode"] == "specialist"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_adapter_action_github_failed(monkeypatch):
    class _Registry:
        @staticmethod
        def get(action_id):
            return CapabilityDescriptor(
                capability_id=action_id,
                type="adapter_action",
                name="add_comment",
                requires_identity_binding=True,
                policy_tags=["github", "comment"],
            )

    async def _fake_execute_adapter_action(action_id, kwargs):
        return {
            "success": False,
            "error": "unsupported github action",
            "result": {},
            "runtime_events": [{"event_type": "task.adapter_action.failed"}],
        }

    monkeypatch.setattr("src.runtime.execution_bus.get_capability_registry", lambda: _Registry())
    monkeypatch.setattr("src.runtime.execution_bus.execute_adapter_action", _fake_execute_adapter_action)

    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={
            "task_type": "adapter_action_task",
            "action_id": "adapter:github:add_comment",
            "kwargs": {"owner": "acme", "repo": "demo", "pull_number": 10, "comment": "x"},
        },
    )
    result = await bus.execute(req)

    assert result.status == "error"
    assert result.output_payload["success"] is False
    assert result.output_payload["error"] == "unsupported github action"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_jira_workflow_review_task(monkeypatch):
    async def _fake_run_jira_workflow_review(payload):
        return {
            "issue_key": payload["issue_key"],
            "reviewed": True,
            "actions_applied": [{"action": "read_issue", "success": True}],
            "comment_added": True,
            "assignee_updated": payload.get("assignee"),
            "transitioned_to": payload.get("transition"),
            "updated_fields": payload.get("fields") or {},
            "workflow_outcome": "approved",
            "approved": True,
            "skill_name": payload.get("skill_name"),
            "reassignment_target": payload.get("success_reassign_to"),
            "success": True,
            "error": None,
            "runtime_events": [{"event_type": "task.jira_workflow_review.completed"}],
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_jira_workflow_review", _fake_run_jira_workflow_review)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={
            "task_type": "jira_workflow_review_task",
            "issue_key": "PROJ-55",
            "skill_name": "review_skill",
            "success_transition": "Done",
            "success_reassign_to": "reporter",
            "review_comment": "ok",
            "transition": "Done",
            "assignee": "bob",
            "fields": {"summary": "x"},
        },
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["task_type"] == "jira_workflow_review_task"
    assert result.output_payload["result"]["issue_key"] == "PROJ-55"
    assert result.output_payload["workflow_outcome"] == "approved"
    assert result.output_payload["actions_applied"] == [{"action": "read_issue", "success": True}]


@pytest.mark.asyncio
async def test_execution_bus_task_events_include_task_id_for_jira_workflow(monkeypatch):
    async def _fake_run_jira_workflow_review(payload):
        return {
            "issue_key": payload["issue_key"],
            "success": True,
            "error": None,
            "runtime_events": [{"event_type": "task.jira_workflow_review.started"}],
            "actions_applied": [],
            "workflow_outcome": "approved",
            "approved": True,
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_jira_workflow_review", _fake_run_jira_workflow_review)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        metadata={"task_id": "task-jira-1"},
        input_payload={"task_type": "jira_workflow_review_task", "issue_key": "PROJ-55"},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert any(evt.get("event_type") == "task.jira_workflow_review.completed" and evt.get("task_id") == "task-jira-1" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_execution_bus_task_handler_github_review_task_success(monkeypatch):
    captured = {}

    async def _fake_run_github_review_task(payload):
        captured.update(payload)
        return {
            "success": True,
            "error": None,
            "review_summary": "LGTM with minor suggestions",
            "review_event": "APPROVE",
            "review_written": True,
            "comment_written": True,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "runtime_events": [{"event_type": "task.github_review.completed"}],
            "result": {"skill": {"name": "review-pull-request"}},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 42},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["task_type"] == "github_review_task"
    assert result.output_payload["comment_written"] is True
    assert result.output_payload["review_event"] == "APPROVE"
    assert result.output_payload["success"] is True
    assert any(evt.get("event_type") == "task.github_review.completed" for evt in result.runtime_events)
    assert captured["owner"] == "acme"
    assert captured["repo"] == "demo"
    assert captured["pull_number"] == 42


@pytest.mark.asyncio
async def test_execution_bus_github_review_task_propagates_automation_trace(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": True,
            "error": None,
            "review_summary": "ok",
            "review_event": "COMMENT",
            "review_written": True,
            "comment_written": True,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "source": "automation_rule",
            "rule_id": "rule-1",
            "automation_rule_id": "rule-1",
            "dedupe_key": "dedupe-full",
            "review_target": {"type": "team", "name": "acme/reviewers"},
            "runtime_events": [],
            "result": {},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={
            "task_type": "github_review_task",
            "owner": "acme",
            "repo": "demo",
            "pull_number": 42,
            "source": "automation_rule",
            "rule_id": "rule-1",
            "automation_rule_id": "rule-1",
            "dedupe_key": "dedupe-full",
            "review_target": {"type": "team", "name": "acme/reviewers"},
        },
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["source"] == "automation_rule"
    assert result.output_payload["rule_id"] == "rule-1"
    assert result.output_payload["automation_rule_id"] == "rule-1"
    assert result.output_payload["dedupe_key"] == "dedupe-full"
    assert result.output_payload["review_target"] == {"type": "team", "name": "acme/reviewers"}
    task_events = [evt for evt in result.runtime_events if evt.get("event_type") == "task.github_review.completed"]
    assert task_events
    detail = task_events[-1].get("detail_payload") or {}
    assert detail.get("automation_rule_id") == "rule-1"
    assert detail.get("dedupe_key") == "dedupe-full"
    assert detail.get("rule_id") == "rule-1"
    assert detail.get("source") == "automation_rule"


@pytest.mark.asyncio
async def test_execution_bus_github_review_task_approved_event_reflected_in_output(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": True,
            "error": None,
            "review_summary": "approved",
            "review_event": "APPROVE",
            "review_written": True,
            "comment_written": True,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "runtime_events": [],
            "result": {},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 42},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["review_event"] == "APPROVE"
    assert result.output_payload["secondary_action_id"] == "adapter:github:review_pull_request"


@pytest.mark.asyncio
async def test_execution_bus_github_review_task_request_changes_event_reflected_in_output(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": True,
            "error": None,
            "review_summary": "needs changes",
            "review_event": "REQUEST_CHANGES",
            "review_written": True,
            "comment_written": True,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "runtime_events": [],
            "result": {},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 42},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["review_event"] == "REQUEST_CHANGES"


@pytest.mark.asyncio
async def test_execution_bus_github_review_task_comment_event_reflected_in_output(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": True,
            "error": None,
            "review_summary": "comment",
            "review_event": "COMMENT",
            "review_written": True,
            "comment_written": True,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "runtime_events": [],
            "result": {},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 42},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["review_event"] == "COMMENT"


@pytest.mark.asyncio
async def test_execution_bus_task_events_include_task_id_for_github_review(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {"success": True, "error": None, "review_summary": "ok", "review_event": "COMMENT", "review_written": True, "comment_written": True, "secondary_action_attempted": True, "secondary_action_success": True, "secondary_action_id": "adapter:github:review_pull_request", "runtime_events": []}

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        metadata={"task_id": "task-gh-1"},
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 1},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert any(evt.get("event_type") == "task.github_review.completed" and evt.get("task_id") == "task-gh-1" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_execution_bus_task_handler_github_review_task_comment_writeback_failure(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": False,
            "error": "permission denied",
            "review_summary": "Needs changes",
            "review_event": "REQUEST_CHANGES",
            "review_written": False,
            "comment_written": False,
            "secondary_action_attempted": True,
            "secondary_action_success": False,
            "secondary_action_id": "adapter:github:review_pull_request",
            "runtime_events": [{"event_type": "task.github_review.failed"}],
            "result": {},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 42},
    )
    result = await bus.execute(req)

    assert result.status == "error"
    assert result.output_payload["success"] is False
    assert result.output_payload["comment_written"] is False
    assert result.output_payload["error"] == "permission denied"
    assert any(evt.get("event_type") == "task.github_review.failed" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_execution_bus_task_tool_events_include_task_id(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return "done"

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        metadata={"task_id": "task-tool-1"},
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert any(evt.get("event_type") == "task.tool.completed" and evt.get("task_id") == "task-tool-1" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_execution_bus_terminal_lifecycle_events_include_task_id(monkeypatch):
    emitted = []

    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return "done"

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus(event_emitter=lambda event_type, payload: emitted.append((event_type, payload)))
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        metadata={"task_id": "task-lifecycle-1"},
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    terminal_payload = emitted[-1][1]
    assert terminal_payload.get("event_type") == "execution.completed"
    assert terminal_payload.get("task_id") == "task-lifecycle-1"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_github_review_task_missing_required_fields():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo"},
    )
    result = await bus.execute(req)

    assert result.status == "error"
    assert "Missing required input_payload field: pull_number" in result.output_payload["error"]


@pytest.mark.asyncio
async def test_execution_bus_task_handler_accepts_execution_result(monkeypatch):
    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        return make_execution_result(
            request_id="inner-1",
            status="success",
            output_payload={"response": "inner-ok"},
            artifacts={"a": 1},
            runtime_events=[{"evt": "x"}],
            next_action_hint="next",
            audit_ref="audit-1",
        )

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert result.output_payload["content"] == "inner-ok"
    assert result.output_payload["result"]["response"] == "inner-ok"
    assert result.artifacts == {"a": 1}
    assert result.runtime_events[0] == {"evt": "x"}
    assert any(evt.get("event_type") == "task.tool.completed" for evt in result.runtime_events if isinstance(evt, dict))
    assert result.next_action_hint == "next"
    assert result.audit_ref == "audit-1"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_falls_back_to_request_id_as_session(monkeypatch):
    captured = {}

    async def _fake_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        captured["session_id"] = session_id
        return ToolResult(success=True, content=f"{tool_name}:{session_id}", error=None)

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _fake_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        request_id="req-fallback",
        source_type="agent",
        execution_type="task",
        session_id=None,
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {"x": 1}},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert captured["session_id"] == "req-fallback"


@pytest.mark.asyncio
async def test_execution_bus_task_handler_exception_uses_outer_error_path(monkeypatch):
    async def _failing_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        raise RuntimeError("task exploded")

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _failing_run_tool_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)

    assert result.status == "error"
    assert result.output_payload["error"] == "task exploded"
    assert result.output_payload["error_type"] == "RuntimeError"
    assert result.output_payload["execution_type"] == "task"


@pytest.mark.asyncio
async def test_execution_bus_governance_on_error_invoked_for_task_exception(monkeypatch):
    class _RecordingGovernance(GovernanceHooks):
        def __init__(self):
            self.error_seen = None

        def on_error(self, request, error):
            self.error_seen = (request.execution_type, error.__class__.__name__)

    async def _failing_run_tool_task(*, session_id, tool_name, coro_factory, event_callback=None):
        raise RuntimeError("task exploded")

    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _failing_run_tool_task)
    bus = build_default_execution_bus(governance=_RecordingGovernance())
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        session_id="s-task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool", "kwargs": {}},
    )
    result = await bus.execute(req)

    assert result.status == "error"
    assert bus._governance.error_seen == ("task", "RuntimeError")


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
    assert calls == [
        ("tool_a", {"v": 1, "_session_id": "s-injected"}),
        ("tool_b", {"v": 2, "_session_id": "s-injected"}),
    ]


@pytest.mark.asyncio
async def test_execution_bus_persists_last_execution_id_only_when_opted_in(monkeypatch):
    calls = []

    async def _record_set_last_execution_id(session_id, request_id):
        calls.append((session_id, request_id))

    monkeypatch.setattr("src.sessions.manager.session_manager.set_last_execution_id", _record_set_last_execution_id)

    async def _ok(_request):
        return {"response": "ok"}

    bus = build_default_execution_bus(chat_handler=_ok)
    no_persist_req = make_execution_request(
        source_type="chat",
        execution_type="chat",
        session_id="s-no-persist",
        metadata={},
    )
    await bus.execute(no_persist_req)
    assert calls == []

    persist_req = make_execution_request(
        source_type="chat",
        execution_type="chat",
        session_id="s-persist",
        metadata={"persist_last_execution_id": True},
    )
    await bus.execute(persist_req)
    assert calls == [("s-persist", persist_req.request_id)]


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

    captured = {}

    async def _fake_execute_skill_orchestration(*, source_ref, session_id, input_payload, metadata=None):
        captured["source_ref"] = source_ref
        captured["session_id"] = session_id
        captured["input_payload"] = dict(input_payload)
        captured["metadata"] = dict(metadata or {})
        return make_execution_result(
            request_id="req-skill",
            status="success",
            output_payload={"output": "skill-ok", "data": {"x": 1}},
        )

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.execute_skill_orchestration", _fake_execute_skill_orchestration)
    result = await executor.execute_skill("demo_skill", message="hello")

    assert captured["input_payload"]["skill_name"] == "demo_skill"
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
    assert captured["parent_session_id"] == "s-sub"


def test_execution_bus_copies_handlers_mapping():
    async def handler(_request):
        return {"response": "ok"}

    provided = {"chat": handler}
    bus = ExecutionBus(handlers=provided)
    provided.clear()
    assert "chat" in bus._handlers


@pytest.mark.asyncio
async def test_execution_bus_adapter_action_task_includes_capability_metadata(monkeypatch):
    class _Registry:
        def get(self, action_id):
            return CapabilityDescriptor(
                capability_id=action_id,
                type="adapter_action",
                name="read_issue",
                policy_tags=["jira", "read"],
                requires_identity_binding=True,
            )

    async def _fake_execute_adapter_action(_action_id, _kwargs):
        return {"success": True, "error": None, "result": {"ok": True}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.execution_bus.get_capability_registry", lambda: _Registry())
    monkeypatch.setattr("src.runtime.execution_bus.execute_adapter_action", _fake_execute_adapter_action)

    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "adapter_action_task", "action_id": "adapter:jira:read_issue", "kwargs": {"issue_key": "ENG-1"}},
        metadata={"identity_binding_system_type": "jira", "identity_binding_external_account_id": "acct-1"},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["task_type"] == "adapter_action_task"
    assert result.output_payload["action_id"] == "adapter:jira:read_issue"
    assert result.output_payload["success"] is True
    assert result.output_payload["error"] is None
    assert result.output_payload["result"] == {"ok": True}
    assert result.output_payload["capability_id"] == "adapter:jira:read_issue"
    assert result.output_payload["capability_type"] == "adapter_action"
    assert result.output_payload["requires_identity_binding"] is True
    assert result.output_payload["capability_resolution"] == "resolved"
    assert result.output_payload["policy_tags"] == ["jira", "read"]


@pytest.mark.asyncio
async def test_execution_bus_task_capability_unresolved_fallback_for_non_adapter_task(monkeypatch):
    async def _fake_review(_payload):
        return {"success": True, "runtime_events": [], "workflow_outcome": "approved", "actions_applied": []}

    class _Registry:
        def get(self, _capability_id):
            return None

    monkeypatch.setattr("src.runtime.execution_bus.get_capability_registry", lambda: _Registry())
    monkeypatch.setattr("src.runtime.execution_bus.run_jira_workflow_review", _fake_review)

    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "jira_workflow_review_task", "issue_key": "ENG-9"},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["capability_resolution"] == "unresolved"


@pytest.mark.asyncio
async def test_github_review_task_includes_involved_capability_ids(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {"success": True, "review_summary": "ok", "review_event": "COMMENT", "review_written": True, "error": None, "comment_written": True, "secondary_action_attempted": True, "secondary_action_success": True, "secondary_action_id": "adapter:github:review_pull_request", "runtime_events": []}

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 7},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert "skill:review-pull-request" in result.output_payload["involved_capability_ids"]
    assert "adapter:github:review_pull_request" in result.output_payload["involved_capability_ids"]
    assert result.output_payload["governed_secondary_action_ids"] == ["adapter:github:review_pull_request"]
    assert result.output_payload["blocked_secondary_action_ids"] == []
    assert result.output_payload["applied_secondary_action_ids"] == ["adapter:github:review_pull_request"]
    decisions = result.output_payload["secondary_action_decisions"]
    assert decisions and decisions[0]["decision"] == "applied"


@pytest.mark.asyncio
async def test_jira_workflow_review_task_includes_involved_capability_ids(monkeypatch):
    async def _fake_run_review(_payload):
        return {"success": True, "workflow_outcome": "approved", "actions_applied": [], "runtime_events": []}

    monkeypatch.setattr("src.runtime.execution_bus.run_jira_workflow_review", _fake_run_review)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "jira_workflow_review_task",
            "issue_key": "ENG-3",
            "success_transition": "Done",
            "explicit_success_assignee": "u1",
            "review_comment": "LGTM",
            "fields": {"summary": "x"},
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    involved = set(result.output_payload["involved_capability_ids"])
    assert {"adapter:jira:read_issue", "adapter:jira:transition_issue", "adapter:jira:assign_issue", "adapter:jira:add_comment", "adapter:jira:update_issue"}.issubset(involved)


@pytest.mark.asyncio
async def test_github_review_task_secondary_action_denied_by_capability_policy(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": False,
            "review_summary": "ok",
            "error": "capability policy blocked for secondary action",
            "review_event": "REQUEST_CHANGES",
            "review_written": False,
            "comment_written": False,
            "secondary_action_attempted": True,
            "secondary_action_success": False,
            "secondary_action_id": "adapter:github:review_pull_request",
            "runtime_events": [{"event_type": "task.github_review.secondary_action.blocked"}],
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 7},
        metadata={"denied_actions": ["review_pull_request"]},
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["secondary_action_attempted"] is True
    assert result.output_payload["secondary_action_success"] is False
    assert result.output_payload["secondary_action_id"] == "adapter:github:review_pull_request"
    assert "capability policy blocked for secondary action" in str(result.output_payload["error"])
    assert result.output_payload["governed_secondary_action_ids"] == ["adapter:github:review_pull_request"]
    assert result.output_payload["blocked_secondary_action_ids"] == ["adapter:github:review_pull_request"]
    assert result.output_payload["applied_secondary_action_ids"] == []
    assert result.output_payload["secondary_action_decisions"][0]["decision"] == "blocked"
    assert any(evt.get("event_type") == "task.github_review.secondary_action.blocked" for evt in result.runtime_events)
    assert any(evt.get("event_type") == "governance.audit" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_execution_bus_github_action_gate_returns_normalized_blocked_shape(monkeypatch):
    captured = {}

    async def _fake_run_github_review_task(payload):
        gate = payload["_action_gate"]
        captured["allowed"] = gate("adapter:github:review_pull_request", {"owner": "acme"})
        captured["denied"] = gate("adapter:github:add_comment", {"owner": "acme"})
        return {
            "success": True,
            "review_summary": "ok",
            "review_event": "COMMENT",
            "review_written": True,
            "comment_written": True,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "runtime_events": [],
            "error": None,
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)

    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 88},
        metadata={"allowed_adapter_actions": ["adapter:github:review_pull_request"]},
    )
    result = await bus.execute(req)

    assert result.status == "success"
    assert captured["allowed"] == {"blocked": False}
    assert captured["denied"]["blocked"] is True
    assert captured["denied"]["reason"] == "unsupported_secondary_action"
    assert "Unsupported secondary action" in captured["denied"]["message"]
    assert result.output_payload["secondary_action_decisions"][0]["decision"] == "applied"


@pytest.mark.asyncio
async def test_jira_workflow_review_task_transition_denied_adds_blocked_secondary_fields(monkeypatch):
    async def _fake_run_review(payload):
        gate = payload["_action_gate"]
        gate_result = gate("transition_issue", {"issue_key": payload["issue_key"], "transition": "Done"})
        return {
            "success": True,
            "issue_key": payload["issue_key"],
            "workflow_outcome": "approved",
            "approved": True,
            "reassignment_target": None,
            "actions_applied": [
                {"action": "transition_issue", "success": False, "blocked": True, "error": gate_result.get("error")},
            ],
            "runtime_events": [],
            "error": None,
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_jira_workflow_review", _fake_run_review)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "jira_workflow_review_task", "issue_key": "ENG-50", "success_transition": "Done"},
        metadata={"denied_actions": ["transition_issue"]},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert "adapter:jira:transition_issue" in result.output_payload["blocked_secondary_action_ids"]
    assert result.output_payload["applied_secondary_action_ids"] == []
    assert any(item.get("decision") == "blocked" for item in result.output_payload["secondary_action_decisions"])
    assert any(evt.get("event_type") == "task.jira_workflow_review.secondary_action.blocked" for evt in result.runtime_events)
    assert any(evt.get("event_type") == "governance.audit" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_jira_workflow_review_task_comment_and_assign_denied(monkeypatch):
    async def _fake_run_review(payload):
        gate = payload["_action_gate"]
        comment_gate = gate("add_comment", {"issue_key": payload["issue_key"], "comment": "hi"})
        assign_gate = gate("assign_issue", {"issue_key": payload["issue_key"], "assignee": "u1"})
        return {
            "success": False,
            "issue_key": payload["issue_key"],
            "workflow_outcome": "rejected",
            "approved": False,
            "reassignment_target": "u1",
            "actions_applied": [
                {"action": "add_comment", "success": False, "blocked": True, "error": comment_gate.get("error")},
                {"action": "assign_issue", "success": False, "blocked": True, "error": assign_gate.get("error")},
            ],
            "runtime_events": [],
            "error": "assignment_failed",
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_jira_workflow_review", _fake_run_review)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "jira_workflow_review_task",
            "issue_key": "ENG-51",
            "review_comment": "need change",
            "explicit_success_assignee": "u1",
        },
        metadata={"denied_actions": ["add_comment", "assign_issue"]},
    )
    result = await bus.execute(req)
    assert result.status == "error"
    blocked = set(result.output_payload["blocked_secondary_action_ids"])
    assert {"adapter:jira:add_comment", "adapter:jira:assign_issue"}.issubset(blocked)
    decision_actions = {item.get("action_id"): item.get("decision") for item in result.output_payload["secondary_action_decisions"]}
    assert decision_actions.get("adapter:jira:add_comment") == "blocked"
    assert decision_actions.get("adapter:jira:assign_issue") == "blocked"


@pytest.mark.asyncio
async def test_github_review_task_secondary_action_allowed_by_allowed_adapter_actions(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {"success": True, "review_summary": "ok", "review_event": "COMMENT", "review_written": True, "error": None, "comment_written": True, "secondary_action_attempted": True, "secondary_action_success": True, "secondary_action_id": "adapter:github:review_pull_request", "runtime_events": []}

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 8},
        metadata={"allowed_adapter_actions": ["adapter:github:review_pull_request"]},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["secondary_action_decisions"][0]["decision"] == "applied"


@pytest.mark.asyncio
async def test_github_review_task_secondary_action_allowed_by_allowed_actions_alias(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {"success": True, "review_summary": "ok", "review_event": "COMMENT", "review_written": True, "error": None, "comment_written": True, "secondary_action_attempted": True, "secondary_action_success": True, "secondary_action_id": "adapter:github:review_pull_request", "runtime_events": []}

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 9},
        metadata={"allowed_actions": ["review_pull_request"]},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["secondary_action_decisions"][0]["decision"] == "applied"


@pytest.mark.asyncio
async def test_jira_workflow_review_task_comment_denied_has_governance_audit_event(monkeypatch):
    async def _fake_run_review(payload):
        gate = payload["_action_gate"]
        gate_result = gate("add_comment", {"issue_key": payload["issue_key"], "comment": "hi"})
        return {
            "success": True,
            "issue_key": payload["issue_key"],
            "workflow_outcome": "approved",
            "approved": True,
            "actions_applied": [{"action": "add_comment", "success": False, "blocked": True, "error": gate_result.get("error")}],
            "runtime_events": [],
            "error": None,
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_jira_workflow_review", _fake_run_review)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "jira_workflow_review_task", "issue_key": "ENG-52", "review_comment": "x"},
        metadata={"denied_actions": ["add_comment"]},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert any(evt.get("event_type") == "governance.audit" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_github_review_task_portal_style_metadata_with_explainability_fields(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {"success": True, "review_summary": "ok", "review_event": "COMMENT", "review_written": True, "error": None, "comment_written": True, "secondary_action_attempted": True, "secondary_action_success": True, "secondary_action_id": "adapter:github:review_pull_request", "runtime_events": []}

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 10},
        metadata={
            "capability_profile_id": "cap-1",
            "policy_profile_id": "policy-1",
            "allowed_capability_ids": ["skill:review-pull-request", "adapter:github:review_pull_request"],
            "allowed_capability_types": ["action"],
            "allowed_actions": ["review_pull_request"],
            "allowed_adapter_actions": ["adapter:github:review_pull_request"],
            "unresolved_actions": ["foo_action"],
            "resolved_action_mappings": {"foo_action": "adapter:github:add_comment"},
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["secondary_action_decisions"][0]["decision"] == "applied"


@pytest.mark.asyncio
async def test_github_review_task_explicit_issue_comment_fallback_secondary_action(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": True,
            "review_summary": "ok",
            "review_event": "COMMENT",
            "review_written": True,
            "error": None,
            "comment_written": True,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:add_comment",
            "runtime_events": [],
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "github_review_task",
            "owner": "acme",
            "repo": "demo",
            "pull_number": 11,
            "writeback_mode": "issue_comment",
        },
        metadata={"allowed_adapter_actions": ["adapter:github:add_comment"]},
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert result.output_payload["secondary_action_id"] == "adapter:github:add_comment"
    assert result.output_payload["governed_secondary_action_ids"] == ["adapter:github:add_comment"]


@pytest.mark.asyncio
async def test_github_review_task_superseded_fields_passthrough(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": False,
            "review_summary": "stale",
            "review_event": "COMMENT",
            "review_written": False,
            "comment_written": False,
            "error": "superseded_by_new_head_sha",
            "error_code": "superseded_by_new_head_sha",
            "stale": True,
            "expected_head_sha": "sha-old",
            "current_head_sha": "sha-new",
            "secondary_action_attempted": False,
            "secondary_action_success": False,
            "secondary_action_id": "adapter:github:review_pull_request",
            "source": "automation_rule",
            "rule_id": "rule-2",
            "automation_rule_id": "rule-2",
            "dedupe_key": "dedupe-stale",
            "review_target": {"type": "team", "name": "acme/reviewers"},
            "runtime_events": [{"event_type": "task.github_review.superseded", "state": "stale"}],
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 14},
    )
    result = await bus.execute(req)
    assert result.status == "error"
    assert result.output_payload["error_code"] == "superseded_by_new_head_sha"
    assert result.output_payload["stale"] is True
    assert result.output_payload["expected_head_sha"] == "sha-old"
    assert result.output_payload["current_head_sha"] == "sha-new"
    assert result.output_payload["automation_rule_id"] == "rule-2"
    assert result.output_payload["rule_id"] == "rule-2"
    assert result.output_payload["dedupe_key"] == "dedupe-stale"
    assert result.output_payload["review_target"] == {"type": "team", "name": "acme/reviewers"}
    assert result.output_payload["source"] == "automation_rule"
    task_events = [evt for evt in result.runtime_events if evt.get("event_type") == "task.github_review.failed"]
    assert task_events
    detail = task_events[-1].get("detail_payload") or {}
    assert detail.get("error_code") == "superseded_by_new_head_sha"
    assert detail.get("stale") is True
    assert detail.get("automation_rule_id") == "rule-2"
    assert detail.get("dedupe_key") == "dedupe-stale"


@pytest.mark.asyncio
async def test_jira_workflow_review_task_portal_style_metadata_deny_transition(monkeypatch):
    async def _fake_run_review(payload):
        gate = payload["_action_gate"]
        gate_result = gate("transition_issue", {"issue_key": payload["issue_key"], "transition": "Done"})
        return {
            "success": True,
            "issue_key": payload["issue_key"],
            "workflow_outcome": "approved",
            "approved": True,
            "actions_applied": [{"action": "transition_issue", "success": False, "blocked": True, "error": gate_result.get("error")}],
            "runtime_events": [],
            "error": None,
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_jira_workflow_review", _fake_run_review)
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "jira_workflow_review_task", "issue_key": "ENG-53", "success_transition": "Done"},
        metadata={
            "capability_profile_id": "cap-jira",
            "policy_profile_id": "policy-jira",
            "allowed_capability_ids": ["adapter:jira:read_issue"],
            "allowed_capability_types": ["action"],
            "denied_actions": ["transition_issue"],
            "unresolved_actions": ["old_transition"],
            "resolved_action_mappings": {"old_transition": "adapter:jira:transition_issue"},
        },
    )
    result = await bus.execute(req)
    assert result.status == "success"
    assert "adapter:jira:transition_issue" in result.output_payload["blocked_secondary_action_ids"]
    assert any(item.get("decision") == "blocked" for item in result.output_payload["secondary_action_decisions"])
    assert any(evt.get("event_type") == "governance.audit" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_run_skill_execution_routes_through_execution_bus_by_default(monkeypatch):
    async def _fake_orchestration(**_kwargs):
        return make_execution_result(
            request_id="req-bus",
            status="success",
            output_payload={"output": "bus-path", "data": {"source": "bus"}},
        )

    async def _should_not_be_called(*_args, **_kwargs):
        raise AssertionError("legacy direct execution should not be used by default")

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.execute_skill_orchestration", _fake_orchestration)
    monkeypatch.setattr("src.agents.executor.skills_executor.execute_skill", _should_not_be_called)
    monkeypatch.delenv("EFP_ALLOW_LEGACY_DIRECT_EXECUTION", raising=False)

    result = await run_skill_execution("demo_skill", input="hello")
    assert result.success is True
    assert result.output == "bus-path"
    assert result.data.get("source") == "bus"


@pytest.mark.asyncio
async def test_run_skill_execution_legacy_direct_opt_in(monkeypatch):
    async def _fake_direct(skill_name, **_kwargs):
        return SkillResult(success=True, output=f"legacy:{skill_name}")

    async def _should_not_be_called(**_kwargs):
        raise AssertionError("execution bus path should not be used in legacy direct opt-in mode")

    monkeypatch.setattr("src.agents.executor.skills_executor.execute_skill", _fake_direct)
    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.execute_skill_orchestration", _should_not_be_called)
    monkeypatch.setenv("EFP_ALLOW_LEGACY_DIRECT_EXECUTION", "true")

    result = await run_skill_execution("demo_skill", input="hello")
    assert result.success is True
    assert result.output == "legacy:demo_skill"


@pytest.mark.asyncio
async def test_unknown_task_type_still_returns_unsupported_blocked():
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "unknown_task_type_x"},
    )

    result = await build_default_execution_bus().execute(req)

    assert result.status == "blocked"
    assert "Unsupported task_type" in result.output_payload["error"]


def test_task_template_registry_lists_expected_templates():
    template_ids = {item.template_id for item in list_task_templates()}
    assert template_ids == {
        "collect_requirements_to_bundle",
        "design_test_cases_from_bundle",
        "collect_research_notes_to_bundle",
        "generate_implementation_plan_from_bundle",
        "generate_runbook_from_bundle",
        "github_pr_review",
        "github_comment_mention",
    }


@pytest.mark.asyncio
async def test_bundle_action_task_routes_to_task_template_skill(monkeypatch):
    observed = {}

    async def _fake_run_skill_execution(skill_name, **kwargs):
        observed["skill_name"] = skill_name
        observed["kwargs"] = kwargs
        return SkillResult(success=True, output="ok", data={"bundle_ref": kwargs.get("bundle_ref"), "updated_files": []})

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "bundle_action_task",
            "task_template_id": "collect_requirements_to_bundle",
            "bundle_template_id": "requirement.v1",
            "bundle_ref": {"repo": "org/assets", "path": "bundles/RB-1", "branch": "main"},
            "manifest_ref": {"repo": "org/assets", "path": "bundles/RB-1/bundle.yaml", "branch": "main"},
            "sources": {"jira": ["ABC-1"]},
            "skill_name": "should_not_override",
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert observed["skill_name"] == "collect_requirements_to_bundle"
    assert result.status == "success"
    assert result.output_payload["task_template_id"] == "collect_requirements_to_bundle"
    assert any(
        evt.get("detail_payload", {}).get("task_template_id") == "collect_requirements_to_bundle"
        for evt in result.runtime_events
        if isinstance(evt, dict)
    )


@pytest.mark.asyncio
async def test_bundle_action_task_template_id_fallback_does_not_accept_bundle_template_id():
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "bundle_action_task",
            "template_id": "requirement.v1",
            "bundle_template_id": "requirement.v1",
            "bundle_ref": {"repo": "org/assets", "path": "bundles/RB-1", "branch": "main"},
            "manifest_ref": {"repo": "org/assets", "path": "bundles/RB-1/bundle.yaml", "branch": "main"},
            "sources": {"jira": ["ABC-1"]},
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "blocked"
    assert "Unsupported task_template_id" in result.output_payload["error"]


@pytest.mark.asyncio
async def test_bundle_action_task_metadata_portal_task_template_id_fallback_succeeds(monkeypatch):
    observed = {}

    async def _fake_run_skill_execution(skill_name, **kwargs):
        observed["skill_name"] = skill_name
        observed["kwargs"] = kwargs
        return SkillResult(success=True, output="ok", data={"updated_files": []})

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        metadata={"portal_task_template_id": "collect_requirements_to_bundle"},
        input_payload={
            "task_type": "bundle_action_task",
            "bundle_template_id": "requirement.v1",
            "bundle_ref": {"repo": "org/assets", "path": "bundles/RB-1", "branch": "main"},
            "manifest_ref": {"repo": "org/assets", "path": "bundles/RB-1/bundle.yaml", "branch": "main"},
            "sources": {"jira": ["ABC-1"]},
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "success"
    assert observed["skill_name"] == "collect_requirements_to_bundle"
    assert result.output_payload["task_template_id"] == "collect_requirements_to_bundle"


@pytest.mark.asyncio
async def test_bundle_action_task_without_task_template_id_and_metadata_is_blocked():
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "bundle_action_task",
            "bundle_template_id": "requirement.v1",
            "bundle_ref": {"repo": "org/assets", "path": "bundles/RB-1", "branch": "main"},
            "manifest_ref": {"repo": "org/assets", "path": "bundles/RB-1/bundle.yaml", "branch": "main"},
            "sources": {"jira": ["ABC-1"]},
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "blocked"
    assert "Unsupported task_template_id" in result.output_payload["error"]


@pytest.mark.asyncio
async def test_bundle_action_task_unknown_task_template_blocked(monkeypatch):
    called = {"value": False}

    async def _fake_run_skill_execution(_skill_name, **_kwargs):
        called["value"] = True
        return SkillResult(success=True)

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "bundle_action_task", "task_template_id": "unknown_task_template"},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "blocked"
    assert called["value"] is False


@pytest.mark.asyncio
async def test_bundle_action_task_missing_bundle_ref_blocked():
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "bundle_action_task",
            "task_template_id": "collect_requirements_to_bundle",
            "bundle_template_id": "requirement.v1",
            "manifest_ref": {"repo": "org/assets", "path": "bundles/RB-1/bundle.yaml", "branch": "main"},
            "sources": {"jira": ["ABC-1"]},
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "blocked"
    assert "bundle_ref" in result.output_payload["error"]


@pytest.mark.asyncio
async def test_bundle_action_task_collect_requirements_empty_sources_blocked():
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "bundle_action_task",
            "task_template_id": "collect_requirements_to_bundle",
            "bundle_template_id": "requirement.v1",
            "bundle_ref": {"repo": "org/assets", "path": "bundles/RB-1", "branch": "main"},
            "manifest_ref": {"repo": "org/assets", "path": "bundles/RB-1/bundle.yaml", "branch": "main"},
            "sources": {"jira": []},
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "blocked"
    assert "sources" in result.output_payload["error"]


@pytest.mark.asyncio
async def test_bundle_action_task_design_test_cases_does_not_require_sources(monkeypatch):
    observed = {}

    async def _fake_run_skill_execution(skill_name, **kwargs):
        observed["skill_name"] = skill_name
        observed["kwargs"] = kwargs
        return SkillResult(success=True, output="ok", data={"updated_files": []})

    monkeypatch.setattr("src.runtime.execution_bus.run_skill_execution", _fake_run_skill_execution)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "bundle_action_task",
            "task_template_id": "design_test_cases_from_bundle",
            "bundle_template_id": "requirement.v1",
            "bundle_ref": {"repo": "org/assets", "path": "bundles/RB-1", "branch": "main"},
            "manifest_ref": {"repo": "org/assets", "path": "bundles/RB-1/bundle.yaml", "branch": "main"},
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "success"
    assert observed["skill_name"] == "design_test_cases_from_bundle"


@pytest.mark.asyncio
async def test_bundle_action_task_incompatible_bundle_template_id_blocked():
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "bundle_action_task",
            "task_template_id": "collect_requirements_to_bundle",
            "bundle_template_id": "research.v1",
            "bundle_ref": {"repo": "org/assets", "path": "bundles/RB-1", "branch": "main"},
            "manifest_ref": {"repo": "org/assets", "path": "bundles/RB-1/bundle.yaml", "branch": "main"},
            "sources": {"jira": ["ABC-1"]},
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "blocked"
    assert "incompatible" in result.output_payload["error"]


@pytest.mark.asyncio
async def test_github_review_task_with_task_template_id_succeeds(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": True,
            "review_summary": "ok",
            "review_event": "COMMENT",
            "review_written": True,
            "comment_written": True,
            "error": None,
            "runtime_events": [],
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "result": {},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "github_review_task",
            "task_template_id": "github_pr_review",
            "owner": "acme",
            "repo": "demo",
            "pull_number": 7,
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "success"
    assert result.output_payload["task_template_id"] == "github_pr_review"
    assert any(evt.get("detail_payload", {}).get("task_template_id") == "github_pr_review" for evt in result.runtime_events)


@pytest.mark.asyncio
async def test_github_review_task_uses_metadata_task_template_id_fallback(monkeypatch):
    async def _fake_run_github_review_task(_payload):
        return {
            "success": True,
            "review_summary": "ok",
            "review_event": "COMMENT",
            "review_written": True,
            "comment_written": True,
            "error": None,
            "runtime_events": [],
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "result": {},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        metadata={"portal_task_template_id": "github_pr_review"},
        input_payload={
            "task_type": "github_review_task",
            "owner": "acme",
            "repo": "demo",
            "pull_number": 7,
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "success"
    assert result.output_payload["task_template_id"] == "github_pr_review"
    assert any(evt.get("detail_payload", {}).get("task_template_id") == "github_pr_review" for evt in result.runtime_events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_kind",
    ["github.mention", "jira.assigned", "jira.mention", "confluence.mention"],
)
async def test_execution_bus_triggered_event_task_success(monkeypatch, source_kind):
    async def _fake_run_triggered_event_task(payload):
        assert payload["source_kind"] == source_kind
        return {"success": True, "source_kind": source_kind, "response": "ok"}

    monkeypatch.setattr("src.runtime.execution_bus.run_triggered_event_task", _fake_run_triggered_event_task)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "triggered_event_task", "source_kind": source_kind},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "success"
    assert result.output_payload["task_type"] == "triggered_event_task"
    assert result.output_payload["source_kind"] == source_kind


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_unsupported_source_kind_error(monkeypatch):
    async def _fake_run_triggered_event_task(_payload):
        raise ValueError("Unsupported source_kind: unknown")

    monkeypatch.setattr("src.runtime.execution_bus.run_triggered_event_task", _fake_run_triggered_event_task)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "triggered_event_task", "source_kind": "unknown"},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "error"
    assert "Unsupported source_kind" in result.output_payload["error"]


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_writeback_failure_error(monkeypatch):
    async def _fake_run_triggered_event_task(_payload):
        raise RuntimeError("writeback failed")

    monkeypatch.setattr("src.runtime.execution_bus.run_triggered_event_task", _fake_run_triggered_event_task)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "triggered_event_task", "source_kind": "jira.mention"},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "error"
    assert "writeback failed" in result.output_payload["error"]


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_enriches_source_kind_from_metadata_and_sets_session_fallback(monkeypatch):
    captured = {}

    async def _fake_run_triggered_event_task(payload):
        captured.update(payload)
        return {"success": True, "source_kind": payload["source_kind"], "response": "ok"}

    monkeypatch.setattr("src.runtime.execution_bus.run_triggered_event_task", _fake_run_triggered_event_task)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        session_id=None,
        input_payload={"task_type": "triggered_event_task", "issue_key": "ENG-1"},
        metadata={"source_kind": "jira.mention"},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "success"
    assert captured["source_kind"] == "jira.mention"
    assert captured["session_id"]
    assert captured["task_id"]


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_derives_source_kind_from_portal_metadata(monkeypatch):
    captured = {}

    async def _fake_run_triggered_event_task(payload):
        captured.update(payload)
        return {"success": True, "source_kind": payload["source_kind"], "response": "ok"}

    monkeypatch.setattr("src.runtime.execution_bus.run_triggered_event_task", _fake_run_triggered_event_task)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "triggered_event_task"},
        metadata={"portal_task_source": "jira", "portal_task_trigger": "assigned"},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "success"
    assert captured["source_kind"] == "jira.assigned"


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_github_secondary_action_blocked(monkeypatch):
    async def _fake_process(*, message, session_id, **_kwargs):
        return {"response": "ok"}

    async def _fake_add_comment(*args, **kwargs):
        raise AssertionError("writeback should be blocked by governance gate")

    monkeypatch.setattr("src.runtime.triggered_event_task.agent.process", _fake_process)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "triggered_event_task",
            "source_kind": "github.mention",
            "owner": "acme",
            "repo": "demo",
            "issue_number": 1,
            "body": "@bot",
        },
        metadata={"denied_adapter_actions": ["adapter:github:add_comment"]},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "error"
    assert result.output_payload["blocked_secondary_action_ids"] == ["adapter:github:add_comment"]
    event_types = [evt.get("event_type") for evt in result.runtime_events]
    assert "task.triggered_event.secondary_action.blocked" in event_types
    assert "governance.audit" in event_types


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_jira_secondary_action_blocked(monkeypatch):
    async def _fake_process(*, message, session_id, **_kwargs):
        return {"response": "ok"}

    async def _fake_add_comment(*args, **kwargs):
        raise AssertionError("writeback should be blocked by governance gate")

    monkeypatch.setattr("src.runtime.triggered_event_task.agent.process", _fake_process)
    monkeypatch.setattr("src.runtime.triggered_event_task.jira_channel.add_comment", _fake_add_comment)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "triggered_event_task",
            "source_kind": "jira.assigned",
            "issue_key": "ENG-1",
            "summary": "Feature",
            "status": "Open",
            "assignee": "jira-user",
        },
        metadata={"denied_actions": ["add_comment"]},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "error"
    assert result.output_payload["blocked_secondary_action_ids"] == ["adapter:jira:add_comment"]
    event_types = [evt.get("event_type") for evt in result.runtime_events]
    assert "task.triggered_event.secondary_action.blocked" in event_types
    assert "governance.audit" in event_types


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_confluence_secondary_action_blocked(monkeypatch):
    async def _fake_process(*, message, session_id, **_kwargs):
        return {"response": "ok"}

    async def _fake_add_comment(*args, **kwargs):
        raise AssertionError("writeback should be blocked by governance gate")

    monkeypatch.setattr("src.runtime.triggered_event_task.agent.process", _fake_process)
    monkeypatch.setattr("src.runtime.triggered_event_task.confluence_channel.add_comment", _fake_add_comment)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "triggered_event_task",
            "source_kind": "confluence.mention",
            "page_id": "123",
            "title": "Doc",
            "space_key": "ENG",
            "body": "@bot",
        },
        metadata={"denied_capability_ids": ["channel_action:confluence_add_comment"]},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "error"
    assert result.output_payload["blocked_secondary_action_ids"] == ["channel_action:confluence_add_comment"]
    event_types = [evt.get("event_type") for evt in result.runtime_events]
    assert "task.triggered_event.secondary_action.blocked" in event_types
    assert "governance.audit" in event_types


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_success_includes_secondary_governance_summary(monkeypatch):
    async def _fake_process(*, message, session_id, **_kwargs):
        return {"response": "ok"}

    async def _fake_add_comment(owner, repo, issue_number, body):
        return {"ok": True}

    monkeypatch.setattr("src.runtime.triggered_event_task.agent.process", _fake_process)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={
            "task_type": "triggered_event_task",
            "source_kind": "github.mention",
            "owner": "acme",
            "repo": "demo",
            "issue_number": 5,
            "body": "@bot",
        },
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "success"
    for key in (
        "governed_secondary_action_ids",
        "blocked_secondary_action_ids",
        "applied_secondary_action_ids",
        "secondary_action_decisions",
        "secondary_action_id",
    ):
        assert key in result.output_payload


@pytest.mark.asyncio
async def test_execution_bus_github_review_task_forwards_runtime_request_context(monkeypatch):
    captured = {}

    async def _fake_run_github_review_task(payload):
        captured.update(payload)
        return {
            "success": True,
            "review_summary": "ok",
            "review_event": "COMMENT",
            "review_written": True,
            "comment_written": True,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "runtime_events": [],
            "result": {"skill": {"name": "review-pull-request", "success": True, "output": "ok", "error": None, "data": {"execution_mode": "chat_tool_loop", "chat_session_id": "s", "chat_request_id": "r"}}},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    bus = build_default_execution_bus()
    req = make_execution_request(
        request_id="req-ctx-1",
        session_id="sess-ctx-1",
        agent_id="agent-ctx-1",
        source_type="agent",
        execution_type="task",
        metadata={"task_id": "t1"},
        input_payload={"task_type": "github_review_task", "owner": "acme", "repo": "demo", "pull_number": 1},
    )
    await bus.execute(req)

    assert captured["_runtime_request_id"] == "req-ctx-1"
    assert captured["_runtime_session_id"] == "sess-ctx-1"
    assert captured["_runtime_agent_id"] == "agent-ctx-1"
    assert captured["_execution_metadata"]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_github_review_task_writeback_uses_runtime_session_agent_policy_fallback(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    captured = {}

    async def _fake_chat_loop(**_kwargs):
        return {
            "success": True,
            "output": "## Pull Request Summary\nok",
            "error": None,
            "data": {
                "review_summary": "## Pull Request Summary\nok",
                "execution_mode": "chat_tool_loop",
                "chat_session_id": "chat-s",
                "chat_request_id": "chat-r",
            },
            "runtime_events": [],
        }

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **meta):
        captured["meta"] = meta
        return {"success": True, "error": None, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_chat_loop)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": 1,
            "_execution_metadata": {"policy_profile_id": "policy-1"},
        }
    )

    assert result["success"] is True
    assert captured["meta"]["session_id"] == "chat-s"
    assert captured["meta"]["policy_profile_id"] == "policy-1"


@pytest.mark.asyncio
async def test_github_review_task_writeback_injects_runtime_managed_github_identity_binding(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    captured = {}

    async def _fake_chat_loop(**_kwargs):
        return {
            "success": True,
            "output": "## Pull Request Summary\nok",
            "error": None,
            "data": {
                "review_summary": "## Pull Request Summary\nok",
                "execution_mode": "chat_tool_loop",
                "chat_session_id": "chat-s",
                "chat_request_id": "chat-r",
            },
            "runtime_events": [],
        }

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **meta):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        captured["meta"] = meta
        return {"success": True, "error": None, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_chat_loop)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": 1,
            "_execution_metadata": {
                "task_id": "task-1",
                "portal_task_id": "portal-task-1",
                "external_triggered": True,
                "policy_profile_id": "policy-1",
            },
        }
    )

    assert result["success"] is True
    metadata = captured["meta"]["metadata"]
    assert metadata["identity_binding"]["system_type"] == "github"
    assert metadata["identity_binding"]["id"].startswith("runtime-github-review:")
    assert metadata["identity_binding"]["external_account_id"]
    assert metadata["identity_binding_source"] == "github_review_task"
    assert metadata["identity_binding_runtime_managed"] is True
    assert captured["meta"]["policy_profile_id"] == "policy-1"


@pytest.mark.asyncio
async def test_github_review_task_writeback_preserves_explicit_identity_binding(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    captured = {}

    async def _fake_chat_loop(**_kwargs):
        return {
            "success": True,
            "output": "## Pull Request Summary\nok",
            "error": None,
            "data": {
                "review_summary": "## Pull Request Summary\nok",
                "execution_mode": "chat_tool_loop",
                "chat_session_id": "chat-s",
                "chat_request_id": "chat-r",
            },
            "runtime_events": [],
        }

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **meta):
        captured["meta"] = meta
        return {"success": True, "error": None, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_chat_loop)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    explicit = {"system_type": "github", "id": "explicit-binding-1", "external_account_id": "octocat"}

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": 1,
            "_execution_metadata": {"identity_binding": explicit},
        }
    )

    assert result["success"] is True
    assert captured["meta"]["metadata"]["identity_binding"] == explicit
    assert "identity_binding_runtime_managed" not in captured["meta"]["metadata"]


@pytest.mark.asyncio
async def test_github_review_writeback_real_adapter_bus_path_has_identity_binding(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_chat_loop(**_kwargs):
        return {
            "success": True,
            "output": "## Pull Request Summary\nok",
            "error": None,
            "data": {
                "review_summary": "## Pull Request Summary\nok",
                "execution_mode": "chat_tool_loop",
                "chat_session_id": "chat-s",
                "chat_request_id": "chat-r",
            },
            "runtime_events": [],
        }

    async def _fake_execute_adapter_action(action_id, kwargs):
        return {"success": True, "error": None, "result": {"ok": True}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_chat_loop)
    monkeypatch.setattr("src.runtime.execution_bus.execute_adapter_action", _fake_execute_adapter_action)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": 1,
            "_execution_metadata": {
                "task_id": "task-1",
                "external_triggered": True,
                "allowed_adapter_actions": ["adapter:github:review_pull_request"],
            },
        }
    )

    assert result["success"] is True
    assert result["secondary_action_success"] is True


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_github_review_comment_secondary_action_blocked(monkeypatch):
    async def _fake_process(*, message, session_id, **_kwargs):
        return {"response": "ok"}

    async def _fake_reply(*args, **kwargs):
        raise AssertionError("writeback should be blocked by governance gate")

    monkeypatch.setattr("src.runtime.triggered_event_task.agent.process", _fake_process)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.reply_pr_review_comment", _fake_reply)
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type":"triggered_event_task","source_kind":"github.mention","comment_kind":"pull_request_review_comment","reply_mode":"same_surface","owner":"acme","repo":"demo","pull_number":1,"comment_id":2,"body":"@bot","session_id":"sess"},
        metadata={"denied_adapter_actions":["adapter:github:reply_review_comment"]},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.output_payload["blocked_secondary_action_ids"] == ["adapter:github:reply_review_comment"]
    event_types = [evt.get("event_type") for evt in result.runtime_events]
    assert "task.triggered_event.secondary_action.blocked" in event_types


@pytest.mark.asyncio
async def test_execution_bus_triggered_event_task_github_unsupported_comment_kind_fails_cleanly(monkeypatch):
    async def _fake_process(*, message, session_id, **_kwargs):
        return {"response": "ok"}

    async def _fake_add(*args, **kwargs):
        raise AssertionError("unsupported comment_kind must not fallback to add_comment")

    monkeypatch.setattr("src.runtime.triggered_event_task.agent.process", _fake_process)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add)

    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type":"triggered_event_task","source_kind":"github.mention","comment_kind":"commit_comment","owner":"acme","repo":"demo","comment_id":2,"body":"@bot","session_id":"sess"},
    )
    result = await build_default_execution_bus().execute(req)
    assert result.status == "error"
    assert "Unsupported GitHub mention comment_kind" in str(result.output_payload.get("error"))
