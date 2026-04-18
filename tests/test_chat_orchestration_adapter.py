import pytest

from src.runtime.chat_orchestration_adapter import _execute_with_bus
from src.runtime.contracts import make_execution_result


class _Bus:
    def __init__(self, result):
        self._result = result

    def register_handler(self, _type, _handler):
        return None

    async def execute(self, _request):
        return self._result


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_type", ["task", "skill", "subagent", "event"])
async def test_execute_with_bus_non_chat_commits_progressive_context(monkeypatch, execution_type):
    result = make_execution_result(request_id="r1", status="success", output_payload={"ok": True})
    bus = _Bus(result)
    calls = []

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.build_default_execution_bus", lambda execute_tool_func=None: bus)

    async def _commit(*, session_id, model):
        calls.append((session_id, model))

    monkeypatch.setattr("src.runtime.progressive_context.apply_progressive_context_after_turn", _commit)

    out = await _execute_with_bus(
        request_id="r1",
        source_type="task",
        source_ref="ref",
        execution_type=execution_type,
        session_id="s1",
        context_ref=None,
        input_payload={},
        metadata={},
    )

    assert out is result
    assert calls == [("s1", None)]


@pytest.mark.asyncio
async def test_execute_with_bus_chat_does_not_duplicate_progressive_context_commit(monkeypatch):
    result = make_execution_result(request_id="r2", status="success", output_payload={"ok": True})
    bus = _Bus(result)
    calls = []

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.build_default_execution_bus", lambda execute_tool_func=None: bus)

    async def _commit(*, session_id, model):
        calls.append((session_id, model))

    monkeypatch.setattr("src.runtime.progressive_context.apply_progressive_context_after_turn", _commit)

    out = await _execute_with_bus(
        request_id="r2",
        source_type="chat",
        source_ref="ref",
        execution_type="chat",
        session_id="s2",
        context_ref=None,
        input_payload={},
        metadata={},
    )

    assert out is result
    assert calls == []


@pytest.mark.asyncio
async def test_execute_with_bus_best_effort_failure_does_not_fail_result(monkeypatch):
    result = make_execution_result(request_id="r3", status="success", output_payload={"ok": True})
    bus = _Bus(result)

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.build_default_execution_bus", lambda execute_tool_func=None: bus)

    async def _commit(*, session_id, model):
        raise RuntimeError("commit failed")

    monkeypatch.setattr("src.runtime.progressive_context.apply_progressive_context_after_turn", _commit)

    out = await _execute_with_bus(
        request_id="r3",
        source_type="task",
        source_ref="ref",
        execution_type="task",
        session_id="s3",
        context_ref=None,
        input_payload={},
        metadata={},
    )

    assert out is result
    assert out.status == "success"
