from pathlib import Path

import pytest

from src.runtime.contracts import make_execution_request, make_execution_result
from src.runtime.execution_bus import ExecutionBus
from src.utils.logger import clear_log_context, get_log_context


@pytest.mark.asyncio
async def test_execution_bus_binds_and_resets_log_context(monkeypatch):
    clear_log_context()
    seen_context = {}

    async def handler(request):
        seen_context.update(get_log_context())
        return make_execution_result(request_id=request.request_id, status="success", output_payload={"ok": True})

    bus = ExecutionBus(handlers={"tool": handler})
    request = make_execution_request(
        request_id="req-observe-1",
        source_type="chat",
        source_ref="webchat",
        agent_id="agent-obs",
        session_id="sess-obs",
        execution_type="tool",
        input_payload={"tool_name": "contract_echo"},
        metadata={
            "trace_id": "trace-obs",
            "task_id": "task-obs",
            "portal_task_id": "portal-task-obs",
            "portal_dispatch_id": "dispatch-obs",
            "path": "/api/chat",
            "runtime_type": "native",
            "tool_source": "external_tools_repo",
            "profile_version": "profile-obs",
        },
    )

    result = await bus.execute(request)

    assert result.status == "success"
    assert seen_context["trace_id"] == "trace-obs"
    assert seen_context["request_id"] == "req-observe-1"
    assert seen_context["session_id"] == "sess-obs"
    assert seen_context["task_id"] == "task-obs"
    assert seen_context["portal_task_id"] == "portal-task-obs"
    assert seen_context["portal_dispatch_id"] == "dispatch-obs"
    assert seen_context["agent_id"] == "agent-obs"
    assert seen_context["runtime_type"] == "native"
    assert seen_context["execution_type"] == "tool"
    assert seen_context["source_type"] == "chat"
    assert seen_context["tool_name"] == "contract_echo"
    assert seen_context["tool_source"] == "external_tools_repo"
    assert seen_context["profile_version"] == "profile-obs"
    assert seen_context["path"] == "/api/chat"

    after_context = get_log_context()
    assert after_context["request_id"] == "-"
    assert after_context["session_id"] == "-"
    assert after_context["tool_name"] == "-"


@pytest.mark.asyncio
async def test_execution_bus_resets_log_context_after_handler_failure():
    clear_log_context()

    async def handler(_request):
        raise RuntimeError("boom")

    bus = ExecutionBus(handlers={"tool": handler})
    request = make_execution_request(
        request_id="req-observe-fail",
        source_type="chat",
        source_ref="webchat",
        session_id="sess-fail",
        execution_type="tool",
        input_payload={"tool_name": "contract_echo"},
        metadata={"path": "/api/chat"},
    )

    result = await bus.execute(request)

    assert result.status == "error"
    assert get_log_context()["request_id"] == "-"
    assert get_log_context()["session_id"] == "-"
    assert get_log_context()["tool_name"] == "-"


def test_webchat_does_not_contain_final_test_debug_markers():
    text = Path("src/gateway/webchat.py").read_text(encoding="utf-8")
    marker = "FINAL_TEST_" + "2026_02_10_17_10"
    assert marker not in text
    assert "[" + "FINAL_TEST" + "]" not in text
