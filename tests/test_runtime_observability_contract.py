from pathlib import Path
import re

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
            "skill_name": "smoke-skill",
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
    assert seen_context["skill_name"] == "smoke-skill"
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


def test_gateway_session_endpoints_do_not_expose_debug_version_markers():
    gateway_files = [
        Path("src/gateway/webchat.py"),
        Path("src/gateway/server.py"),
    ]

    for path in gateway_files:
        text = path.read_text(encoding="utf-8")
        assert "FINAL_TEST_" not in text
        assert "[FINAL_TEST]" not in text
        assert "FIXED_2026" not in text
        assert not re.search(r"[\"']_marker[\"']\\s*:", text)
        assert not re.search(r"\\[\\s*[\"']_marker[\"']\\s*\\]\\s*=", text)


def test_chat_stream_cleanup_is_not_duplicated():
    text = Path("src/gateway/webchat.py").read_text(encoding="utf-8")
    # Expected: one cleanup in api_chat and one cleanup in api_chat_stream.
    assert text.count("await _cleanup_one_shot_attachments(session_id, attachment_ids)") == 2


def test_load_session_endpoint_binds_and_clears_log_context():
    text = Path("src/gateway/webchat.py").read_text(encoding="utf-8")
    start = text.index("async def api_load_session")
    next_def = text.find("\\nasync def ", start + 1)
    chunk = text[start:] if next_def == -1 else text[start:next_def]

    assert "clear_log_context()" in chunk
    assert 'path="/api/sessions/{session_id}"' in chunk
    assert 'runtime_type=os.getenv("EFP_RUNTIME_TYPE", "native")' in chunk
    assert 'execution_type="session"' in chunk
    assert 'source_type="webchat"' in chunk
    assert "finally:" in chunk
