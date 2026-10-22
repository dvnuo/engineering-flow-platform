"""workflow.run stages several files for one playbook variable and caps their total."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from efp_runtime.connector_bridge import ConnectorBridgeBroker
from efp_runtime.tools.builtin import create_browser_tool
from efp_runtime.tools.definition import ToolContext


ENABLED_METADATA = {
    "connectors": {"local_bridge": {"enabled": True, "client_id": "tab-1", "protocol_version": 1}},
}


def _context(metadata: dict) -> ToolContext:
    return ToolContext(
        session_id="session-browser",
        request_id="chat-req-1",
        tool_call_id="call-browser-1",
        tool_name="browser",
        run_id="run-1",
        metadata=dict(metadata),
    )


async def _pending(broker: ConnectorBridgeBroker):
    for _ in range(200):
        pending = broker.pending("session-browser")
        if pending:
            return pending[0]
        await asyncio.sleep(0.005)
    raise AssertionError("connector request never became pending")


def _workspace(tmp_path: Path) -> dict:
    workspace = tmp_path / "ws"
    (workspace / "uploads").mkdir(parents=True)
    (workspace / "uploads" / "a.pdf").write_bytes(b"a" * 30)
    (workspace / "uploads" / "b.png").write_bytes(b"b" * 20)
    (workspace / "uploads" / "c.xls").write_bytes(b"c" * 10)
    return {**ENABLED_METADATA, "workspace_root": str(workspace)}


@pytest.mark.asyncio
async def test_a_list_variable_stages_every_file_in_order(tmp_path: Path):
    metadata = _workspace(tmp_path)
    broker = ConnectorBridgeBroker()
    tool = create_browser_tool(broker)
    task = asyncio.create_task(
        tool.execute(
            {
                "action": "workflow.run",
                "params": {
                    "workflow_yaml": "steps: []",
                    "files": {"files": ["uploads/a.pdf", "uploads/b.png"], "cover": "uploads/c.xls"},
                    "max_total_bytes": 60,
                },
            },
            _context(metadata),
        )
    )
    request = await _pending(broker)
    assert request.params["files"] == [
        {"var": "files", "path": "uploads/a.pdf", "name": "a.pdf", "bytes": 30},
        {"var": "files", "path": "uploads/b.png", "name": "b.png", "bytes": 20},
        {"var": "cover", "path": "uploads/c.xls", "name": "c.xls", "bytes": 10},
    ]
    broker.resolve(request.request_id, {"ok": True, "result": {"pass": True, "status": "passed", "steps": []}})
    result = await task
    assert result.success is True


@pytest.mark.asyncio
async def test_the_total_cap_and_empty_lists_are_refused_before_any_request(tmp_path: Path):
    metadata = _workspace(tmp_path)
    broker = ConnectorBridgeBroker()
    tool = create_browser_tool(broker)
    cases = [
        ({"workflow_yaml": "steps: []", "files": {"files": ["uploads/a.pdf", "uploads/b.png"]}, "max_total_bytes": 49}, "files_too_large"),
        ({"workflow_yaml": "steps: []", "files": {"files": []}}, "invalid_args"),
        ({"workflow_yaml": "steps: []", "files": {"files": ["uploads/a.pdf"]}, "max_total_bytes": "lots"}, "invalid_args"),
        ({"workflow_yaml": "steps: []", "files": {"files": [f"uploads/{i}.pdf" for i in range(11)]}}, "too_many_files"),
    ]
    for params, code in cases:
        result = await tool.execute({"action": "workflow.run", "params": params}, _context(metadata))
        assert result.success is False and result.error == code, (params, result.content)
    assert broker.pending("session-browser") == []
    # At the cap exactly, the run goes ahead.
    task = asyncio.create_task(
        tool.execute(
            {"action": "workflow.run", "params": {"workflow_yaml": "steps: []", "files": {"files": ["uploads/a.pdf", "uploads/b.png"]}, "max_total_bytes": 50}},
            _context(metadata),
        )
    )
    request = await _pending(broker)
    broker.resolve(request.request_id, {"ok": True, "result": {"pass": True, "status": "passed", "steps": []}})
    assert (await task).success is True
