import inspect

import pytest

from src.runtime.contracts import make_execution_result


@pytest.mark.asyncio
async def test_execute_tool_via_runtime_bus_propagates_governance_passthrough_hint(monkeypatch):
    from src.agents import core

    async def _fake_execute_tool_or_task_orchestration(**kwargs):
        return make_execution_result(
            request_id="req-1",
            status="success",
            output_payload={"success": True, "content": "ok", "error": None},
            artifacts={"governance": {"tool_result_passthrough_recommended": True}},
        )

    monkeypatch.setattr(core, "execute_tool_or_task_orchestration", _fake_execute_tool_or_task_orchestration)

    result = await core._execute_tool_via_runtime_bus(
        session_id="s-1",
        tool_name="demo_tool",
        args={},
    )

    governance_hint = getattr(result, "_governance", {})
    assert isinstance(governance_hint, dict)
    assert governance_hint.get("tool_result_passthrough_recommended") is True


def test_core_no_longer_directly_calls_should_passthrough_tool_result():
    from src.agents import core

    source = inspect.getsource(core.Agent.process)
    assert "should_passthrough_tool_result(" not in source
