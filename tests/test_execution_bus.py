import pytest

from src import ToolResult
from src.agents.errors import LLMError
from src.runtime.contracts import ExecutionResult, make_execution_request, make_execution_result
from src.runtime.execution_bus import ExecutionBus, build_default_execution_bus
from src.runtime.events import build_runtime_event
from src.runtime.governance import GovernanceHooks
from src.runtime.governance_bus import GovernanceDecision
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
    assert captured["session_id"] == "s-task"


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
    assert result.runtime_events == [{"evt": "x"}]
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
    assert calls == [("tool_a", {"v": 1}), ("tool_b", {"v": 2})]


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
