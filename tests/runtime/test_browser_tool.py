"""Tests for the ``browser`` connector tool and the connector bridge broker."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from efp_runtime import connector_bridge as bridge_module
from efp_runtime.connector_bridge import (
    ConnectorBridgeBroker,
    ConnectorBridgeCancelled,
    ConnectorBridgeTimeout,
    clamp_timeout,
    get_connector_bridge_broker,
)
from efp_runtime.events import RuntimeEvent
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.runtime.agent import BROWSER_TOOL_ID, _config_tool_selection
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.system_prompt import SystemPromptBuilder
from efp_runtime.tools.builtin import browser as browser_module
from efp_runtime.tools.builtin import create_browser_tool, create_core_tool_registry
from efp_runtime.tools.builtin.browser import BROWSER_ACTIONS, CONNECTOR_TYPE
from efp_runtime.tools.definition import ToolContext


ENABLED_METADATA = {
    "connectors": {"local_bridge": {"enabled": True, "client_id": "tab-1", "protocol_version": 1}},
}


def _context(metadata: dict | None = None, **overrides) -> ToolContext:
    kwargs = {
        "session_id": "session-browser",
        "request_id": "chat-req-1",
        "tool_call_id": "call-browser-1",
        "tool_name": "browser",
        "run_id": "run-1",
        "metadata": dict(metadata if metadata is not None else ENABLED_METADATA),
    }
    kwargs.update(overrides)
    return ToolContext(**kwargs)


async def _wait_for_pending(broker: ConnectorBridgeBroker, session_id: str):
    for _ in range(200):
        pending = broker.pending(session_id)
        if pending:
            return pending[0]
        await asyncio.sleep(0.005)
    raise AssertionError("connector request never became pending")


# ---------------------------------------------------------------------------
# broker


def test_clamp_timeout_window():
    assert clamp_timeout(None) == bridge_module.DEFAULT_TIMEOUT_SECONDS
    assert clamp_timeout("abc") == bridge_module.DEFAULT_TIMEOUT_SECONDS
    assert clamp_timeout(1) == bridge_module.MIN_TIMEOUT_SECONDS
    assert clamp_timeout(10_000) == bridge_module.MAX_TIMEOUT_SECONDS
    assert clamp_timeout(42) == 42.0


@pytest.mark.asyncio
async def test_broker_resolve_hands_payload_to_waiter():
    broker = ConnectorBridgeBroker()
    request = broker.create(session_id="s1", connector_type=CONNECTOR_TYPE, action="tab.list")
    assert [item.request_id for item in broker.pending("s1")] == [request.request_id]
    assert broker.pending("other") == []

    waiter = asyncio.create_task(broker.wait(request.request_id))
    await asyncio.sleep(0)
    assert broker.resolve(request.request_id, {"ok": True, "result": {"tabs": []}}) is True
    payload = await waiter
    assert payload == {"ok": True, "result": {"tabs": []}}
    assert broker.pending("s1") == []
    assert broker.resolve(request.request_id, {"ok": True}) is False


@pytest.mark.asyncio
async def test_broker_wait_times_out(monkeypatch):
    monkeypatch.setattr(bridge_module, "MIN_TIMEOUT_SECONDS", 0.01)
    broker = ConnectorBridgeBroker()
    request = broker.create(session_id="s1", connector_type=CONNECTOR_TYPE, action="tab.list", timeout_seconds=0.05)
    with pytest.raises(ConnectorBridgeTimeout):
        await broker.wait(request.request_id)
    assert broker.pending("s1") == []


@pytest.mark.asyncio
async def test_broker_wait_honours_cancel_request(monkeypatch):
    monkeypatch.setattr(bridge_module, "_CANCEL_POLL_SECONDS", 0.01)
    broker = ConnectorBridgeBroker()
    request = broker.create(session_id="s1", connector_type=CONNECTOR_TYPE, action="tab.list")
    with pytest.raises(ConnectorBridgeCancelled):
        await broker.wait(request.request_id, cancel_requested=lambda: True)
    assert broker.pending("s1") == []


@pytest.mark.asyncio
async def test_broker_wait_unknown_request():
    broker = ConnectorBridgeBroker()
    with pytest.raises(KeyError):
        await broker.wait("cr_missing")


def test_default_broker_is_process_wide():
    assert get_connector_bridge_broker() is get_connector_bridge_broker()


# ---------------------------------------------------------------------------
# tool


@pytest.mark.asyncio
async def test_tool_reports_connector_disabled_without_metadata():
    tool = create_browser_tool(ConnectorBridgeBroker())
    result = await tool.execute({"action": "tab.list"}, _context(metadata={}))
    assert result.success is False
    assert result.status == "error"
    assert result.error == "connector_disabled"
    assert result.output["error"]["code"] == "connector_disabled"
    assert "Connectors" in result.content


@pytest.mark.asyncio
async def test_tool_reports_connector_disabled_when_flag_false():
    tool = create_browser_tool(ConnectorBridgeBroker())
    metadata = {"connectors": {"local_bridge": {"enabled": False, "client_id": "tab-1"}}}
    result = await tool.execute({"action": "tab.list"}, _context(metadata=metadata))
    assert result.error == "connector_disabled"


@pytest.mark.asyncio
async def test_tool_rejects_unknown_action():
    tool = create_browser_tool(ConnectorBridgeBroker())
    result = await tool.execute({"action": "page.eval"}, _context())
    assert result.error == "invalid_action"


@pytest.mark.asyncio
async def test_tool_round_trip_publishes_events_and_returns_result():
    broker = ConnectorBridgeBroker()
    events: list[RuntimeEvent] = []
    tool = create_browser_tool(broker, event_publisher=events.append)
    context = _context()

    task = asyncio.create_task(tool.execute({"action": "tab.list"}, context))
    request = await _wait_for_pending(broker, "session-browser")

    assert request.connector_type == CONNECTOR_TYPE
    assert request.action == "tab.list"
    assert request.target_client_id == "tab-1"
    assert request.metadata["tool_call_id"] == "call-browser-1"

    assert len(events) == 1
    requested = events[0]
    assert requested.type == "tool.connector_requested"
    assert requested.session_id == "session-browser"
    assert requested.payload["connector_type"] == CONNECTOR_TYPE
    assert requested.payload["connector_request"]["id"] == request.request_id
    assert requested.payload["connector_request"]["target_client_id"] == "tab-1"
    assert requested.payload["tool_call_id"] == "call-browser-1"
    assert requested.payload["request_id"] == "chat-req-1"

    assert broker.resolve(
        request.request_id,
        {
            "ok": True,
            "result": {
                "tabs": [
                    {"id": "T1", "title": "Jira board", "url": "https://jira.example.test/board", "active": True},
                    {"id": "T2", "title": "Portal", "url": "https://portal.example.test/app"},
                ]
            },
        },
    )
    result = await task

    assert result.success is True
    assert result.status == "success"
    assert result.content.startswith("browser tab.list ok (2 tabs)")
    assert "<page-content>" in result.content and "</page-content>" in result.content
    assert "Jira board" in result.content
    assert result.output["ok"] is True
    assert result.output["action"] == "tab.list"
    assert result.output["request_id"] == request.request_id
    assert result.output["data"]["tabs"][0]["id"] == "T1"

    assert len(events) == 2
    responded = events[1]
    assert responded.type == "tool.connector_responded"
    assert responded.payload["ok"] is True
    assert responded.payload["connector_request_id"] == request.request_id
    assert responded.payload["error_code"] is None


@pytest.mark.asyncio
async def test_tool_passes_bridge_error_through():
    broker = ConnectorBridgeBroker()
    events: list[RuntimeEvent] = []
    tool = create_browser_tool(broker, event_publisher=events.append)
    task = asyncio.create_task(tool.execute({"action": "page.click", "params": {"ref": "e12"}}, _context()))
    request = await _wait_for_pending(broker, "session-browser")
    assert request.params == {"ref": "e12"}
    broker.resolve(
        request.request_id,
        {"ok": False, "error": {"code": "session_busy", "message": "busy", "hint": "retry", "status": 409}},
    )
    result = await task
    assert result.success is False
    assert result.error == "session_busy"
    assert result.output["error"]["hint"] == "retry"
    assert "session_busy" in result.content
    assert events[1].payload["ok"] is False
    assert events[1].payload["error_code"] == "session_busy"


@pytest.mark.asyncio
async def test_tool_times_out_when_nobody_answers(monkeypatch):
    monkeypatch.setattr(bridge_module, "MIN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(browser_module, "_RESPONSE_GRACE_SECONDS", 0.0)
    broker = ConnectorBridgeBroker()
    events: list[RuntimeEvent] = []
    tool = create_browser_tool(broker, event_publisher=events.append)
    result = await tool.execute({"action": "page.snapshot", "timeout_seconds": 0.05}, _context())
    assert result.success is False
    assert result.error == "connector_timeout"
    assert result.output["error"]["code"] == "connector_timeout"
    # The message names both budgets and the hint sends the model to check
    # the bridge once, instead of asserting it is down.
    assert "the bridge had 0 seconds for page.snapshot" in result.output["error"]["message"]
    assert "session.status" in result.output["error"]["hint"]
    assert events[-1].type == "tool.connector_responded"
    assert events[-1].payload["error_code"] == "connector_timeout"
    assert broker.pending("session-browser") == []


class _RecordingBroker(ConnectorBridgeBroker):
    """Answers every request at once and keeps what the tool waited with."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[dict] = []

    async def wait(self, request_id, *, timeout_seconds=None, grace_seconds=0.0, cancel_requested=None):
        self.waits.append({"timeout_seconds": timeout_seconds, "grace_seconds": grace_seconds})
        self.cancel(request_id)
        return {"ok": True, "result": {"tabs": []}}


@pytest.mark.asyncio
async def test_tool_waits_a_grace_beyond_the_bridge_budget():
    # The page relays `timeout_seconds` to the bridge unchanged; the tool's own
    # clock runs longer, or an answer sent at the end of the budget (a wait that
    # never matched, a playbook step that ran long) can never arrive in time.
    broker = _RecordingBroker()
    tool = create_browser_tool(broker)
    result = await tool.execute({"action": "tab.list", "timeout_seconds": 120}, _context())
    assert result.success is True
    assert broker.waits == [{"timeout_seconds": None, "grace_seconds": browser_module._RESPONSE_GRACE_SECONDS}]
    assert browser_module._RESPONSE_GRACE_SECONDS >= 30.0


@pytest.mark.asyncio
async def test_broker_wait_adds_grace_to_the_clamped_timeout(monkeypatch):
    monkeypatch.setattr(bridge_module, "MIN_TIMEOUT_SECONDS", 0.01)
    broker = ConnectorBridgeBroker()
    request = broker.create(session_id="s1", connector_type=CONNECTOR_TYPE, action="tab.list", timeout_seconds=0.02)

    async def answer_late():
        await asyncio.sleep(0.08)
        broker.resolve(request.request_id, {"ok": True})

    asyncio.create_task(answer_late())
    # 0.02 s alone would expire first; the grace keeps the request pending.
    payload = await broker.wait(request.request_id, grace_seconds=1.0)
    assert payload == {"ok": True}
    assert bridge_module._grace("nan") == 0.0
    assert bridge_module._grace(-5) == 0.0
    assert bridge_module._grace(float("inf")) == 0.0
    assert bridge_module._grace(2.5) == 2.5


def test_response_grace_allows_for_the_files_the_page_stages():
    base = browser_module._RESPONSE_GRACE_SECONDS
    assert browser_module._response_grace_seconds({}) == base
    assert browser_module._response_grace_seconds({"files": []}) == base
    ten_mib = [{"path": "uploads/a.pdf", "bytes": 10 * 1024 * 1024}]
    assert browser_module._response_grace_seconds({"files": ten_mib}) == base + 10.0
    huge = [{"path": "uploads/big.zip", "bytes": 900 * 1024 * 1024}]
    assert browser_module._response_grace_seconds({"files": huge}) == base + browser_module._MAX_STAGING_GRACE_SECONDS
    odd = [{"path": "x", "bytes": True}, {"path": "y", "bytes": -3}, "not-a-file"]
    assert browser_module._response_grace_seconds({"files": odd}) == base


@pytest.mark.asyncio
async def test_tool_reports_cancellation(monkeypatch):
    monkeypatch.setattr(bridge_module, "_CANCEL_POLL_SECONDS", 0.01)
    broker = ConnectorBridgeBroker()
    tool = create_browser_tool(broker)
    context = _context(cancel_requested=lambda: True)
    result = await tool.execute({"action": "page.snapshot"}, context)
    assert result.error == "connector_cancelled"


@pytest.mark.asyncio
async def test_screenshot_is_written_to_workspace_not_transcript(tmp_path: Path):
    broker = ConnectorBridgeBroker()
    tool = create_browser_tool(broker)
    metadata = {**ENABLED_METADATA, "workspace_root": str(tmp_path)}
    task = asyncio.create_task(tool.execute({"action": "page.screenshot"}, _context(metadata=metadata)))
    request = await _wait_for_pending(broker, "session-browser")
    encoded = base64.b64encode(b"\xff\xd8not-really-jpeg").decode("ascii")
    broker.resolve(
        request.request_id,
        {"ok": True, "result": {"mime": "image/jpeg", "base64": encoded, "width": 1280, "height": 720}},
    )
    result = await task
    assert result.success is True
    saved = tmp_path / ".efp" / "browser-screenshots" / f"{request.request_id}.jpg"
    assert saved.read_bytes() == b"\xff\xd8not-really-jpeg"
    assert "base64" not in result.output["data"]
    assert result.output["data"]["path"].endswith(f"{request.request_id}.jpg")
    assert "1280x720" in result.content
    assert encoded not in result.content


def test_browser_tool_schema_lists_actions():
    tool = create_browser_tool(ConnectorBridgeBroker())
    assert tool.id == BROWSER_TOOL_ID
    assert tool.input_schema["properties"]["action"]["enum"] == list(BROWSER_ACTIONS)
    assert tool.permission.data["subject_arg"] == "action"
    assert tool.metadata["connector_type"] == CONNECTOR_TYPE


# ---------------------------------------------------------------------------
# registry / runtime wiring


def test_browser_tool_registry_is_opt_in(tmp_path: Path):
    assert "browser" not in create_core_tool_registry(tmp_path).ids()
    registry = create_core_tool_registry(tmp_path, include_browser_tool=True)
    assert "browser" in registry.ids()


def test_agent_runtime_registers_browser_tool_from_config(tmp_path: Path):
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "hi"}]),
        config=RuntimeConfig(workspace_root=str(tmp_path), enable_browser_tool=True),
        store=InMemorySessionStore(),
    )
    assert runtime.tool_runtime.registry.get("browser") is not None
    assert runtime.connector_bridge is get_connector_bridge_broker()
    assert runtime._registered_browser_tool_id() == "browser"

    plain = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "hi"}]),
        config=RuntimeConfig(workspace_root=str(tmp_path)),
        store=InMemorySessionStore(),
    )
    assert plain.tool_runtime.registry.get("browser") is None
    assert plain._registered_browser_tool_id() is None


def test_agent_runtime_publishes_tool_events_on_its_bus(tmp_path: Path):
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "hi"}]),
        config=RuntimeConfig(workspace_root=str(tmp_path), enable_browser_tool=True),
        store=InMemorySessionStore(),
    )
    runtime._publish_runtime_event(RuntimeEvent(type="tool.connector_requested", session_id="s1", payload={}))
    assert [event.type for event in runtime.event_bus.history("s1")] == ["tool.connector_requested"]


def test_allowlist_that_omits_browser_still_gets_it(tmp_path: Path):
    config = RuntimeConfig(workspace_root=str(tmp_path), enabled_tools=["read"], enable_browser_tool=True)
    selection = _config_tool_selection(config, browser_tool_id="browser")
    assert selection.enabled == {"read", "browser"}
    without = _config_tool_selection(config, browser_tool_id=None)
    assert without.enabled == {"read"}


def test_runtime_config_coerces_flag():
    config = RuntimeConfig(enable_browser_tool="yes")
    assert config.enable_browser_tool is True
    assert RuntimeConfig().enable_browser_tool is False


# ---------------------------------------------------------------------------
# system prompt


def test_system_prompt_mentions_local_bridge_only_when_enabled():
    builder = SystemPromptBuilder(workspace_root=None)
    with_connector = builder.build_messages(dict(ENABLED_METADATA))
    texts = [part.text for message in with_connector for part in message.parts]
    assert any("Local bridge" in text and "<page-content>" in text for text in texts)
    assert any(message.metadata.get("kind") == "connectors_context" for message in with_connector)
    # connector_timeout is "the page did not answer", not "the bridge is gone":
    # the rule sends the model to check once before it tells the member to.
    assert any("connector_timeout" in text and "session.status" in text for text in texts)

    without = builder.build_messages({"connectors": {"local_bridge": {"enabled": False}}})
    assert not any(message.metadata.get("kind") == "connectors_context" for message in without)
    assert not any(message.metadata.get("kind") == "connectors_context" for message in builder.build_messages({}))
