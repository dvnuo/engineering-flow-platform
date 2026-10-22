"""Tests for the ``browser`` tool's ``page.upload`` and ``workflow.run`` actions.

Files never travel through the connector request: the tool checks each one
against the workspace and sends ``{path, name, bytes}``; the Portal page pulls
the bytes itself and stages them on the member's computer.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from efp_runtime.connector_bridge import ConnectorBridgeBroker
from efp_runtime.skills.discovery import SkillDiscovery
from efp_runtime.tools.builtin import create_browser_tool
from efp_runtime.tools.builtin.browser import BROWSER_ACTIONS, READ_ONLY_ACTIONS
from efp_runtime.tools.definition import ToolContext


ENABLED_METADATA = {
    "connectors": {"local_bridge": {"enabled": True, "client_id": "tab-1", "protocol_version": 1}},
}

PLAYBOOK = (
    "vars:\n"
    "  base_url: \"\"\n"
    "  file: \"\"\n"
    "  instruction: \"\"\n"
    "steps:\n"
    "  - action: tab.open\n"
    "    url: \"{{vars.base_url}}/analyze\"\n"
    "  - action: page.wait\n"
    "    selector: \"input[type=file]\"\n"
    "  - action: page.upload\n"
    "    locators: [{ selector: \"input[type=file]\" }]\n"
    "    file: \"{{vars.file}}\"\n"
    "  - action: page.extract_schema\n"
    "    fields:\n"
    "      result: { selector: \"[data-testid=result]\", required: true }\n"
)


def _context(metadata: dict | None = None) -> ToolContext:
    return ToolContext(
        session_id="session-browser",
        request_id="chat-req-1",
        tool_call_id="call-browser-1",
        tool_name="browser",
        run_id="run-1",
        metadata=dict(metadata if metadata is not None else ENABLED_METADATA),
    )


def _workspace(tmp_path: Path) -> tuple[Path, dict]:
    workspace = tmp_path / "ws"
    (workspace / "output").mkdir(parents=True)
    (workspace / "output" / "report.pdf").write_bytes(b"%PDF-1.4 " * 50)
    return workspace, {**ENABLED_METADATA, "workspace_root": str(workspace)}


async def _wait_for_pending(broker: ConnectorBridgeBroker, session_id: str):
    for _ in range(200):
        pending = broker.pending(session_id)
        if pending:
            return pending[0]
        await asyncio.sleep(0.005)
    raise AssertionError("connector request never became pending")


async def _run(tool, broker, args, metadata):
    """Start the tool, return the pending request and the task to resolve."""

    task = asyncio.create_task(tool.execute(args, _context(metadata)))
    request = await _wait_for_pending(broker, "session-browser")
    return request, task


def test_new_actions_are_listed_and_not_read_only():
    assert "page.upload" in BROWSER_ACTIONS
    assert "workflow.run" in BROWSER_ACTIONS
    assert "page.upload" not in READ_ONLY_ACTIONS
    assert "workflow.run" not in READ_ONLY_ACTIONS


# ---------------------------------------------------------------------------
# page.upload


@pytest.mark.asyncio
async def test_page_upload_sends_file_metadata_not_bytes(tmp_path: Path):
    workspace, metadata = _workspace(tmp_path)
    broker = ConnectorBridgeBroker()
    events = []
    tool = create_browser_tool(broker, event_publisher=events.append)
    request, task = await _run(
        tool,
        broker,
        {"action": "page.upload", "params": {"ref": "r12", "files": ["output/report.pdf"]}},
        metadata,
    )
    assert request.params["ref"] == "r12"
    assert request.params["files"] == [{"path": "output/report.pdf", "name": "report.pdf", "bytes": 450}]
    assert "%PDF" not in json.dumps(request.to_dict())
    assert "%PDF" not in json.dumps(events[0].payload)
    # The event's arguments keep the model's own params for the timeline.
    assert events[0].payload["arguments"]["params"]["files"] == ["output/report.pdf"]

    broker.resolve(
        request.request_id,
        {
            "ok": True,
            "result": {
                "file_count": 1,
                "files": [{"id": "0a1b", "name": "report.pdf", "bytes": 450}],
                "title": "Productivity Suite",
                "selector": "input[type=file]",
            },
        },
    )
    result = await task
    assert result.success is True
    assert result.content.startswith("browser page.upload ok: 1 file(s) attached on Productivity Suite")
    assert "report.pdf (450 B)" in result.content
    assert result.output["data"]["files"][0]["id"] == "0a1b"


@pytest.mark.asyncio
async def test_page_upload_accepts_absolute_paths_inside_the_workspace_and_clear(tmp_path: Path):
    workspace, metadata = _workspace(tmp_path)
    broker = ConnectorBridgeBroker()
    tool = create_browser_tool(broker)
    request, task = await _run(
        tool,
        broker,
        {"action": "page.upload", "params": {"selector": "input", "files": str(workspace / "output" / "report.pdf")}},
        metadata,
    )
    assert request.params["files"][0]["path"] == "output/report.pdf"
    broker.resolve(request.request_id, {"ok": True, "result": {"file_count": 1, "files": []}})
    await task

    request, task = await _run(
        tool,
        broker,
        {"action": "page.upload", "params": {"selector": "input", "clear": True}},
        metadata,
    )
    assert request.params["files"] == [] and request.params["clear"] is True
    broker.resolve(request.request_id, {"ok": True, "result": {"file_count": 0, "clear": True, "title": "Form"}})
    result = await task
    assert "file input cleared on Form" in result.content


@pytest.mark.asyncio
async def test_page_upload_rejects_bad_files_before_asking_the_page(tmp_path: Path):
    workspace, metadata = _workspace(tmp_path)
    (tmp_path / "outside.txt").write_text("x")
    link = workspace / "output" / "escape.txt"
    try:
        link.symlink_to(tmp_path / "outside.txt")
        has_symlink = True
    except (OSError, NotImplementedError):
        has_symlink = False
    broker = ConnectorBridgeBroker()
    events = []
    small = create_browser_tool(broker, event_publisher=events.append, upload_max_bytes=10)
    tool = create_browser_tool(broker, event_publisher=events.append)

    cases = [
        (tool, {"ref": "r1", "files": ["output/missing.pdf"]}, metadata, "file_not_found"),
        (tool, {"ref": "r1", "files": ["../outside.txt"]}, metadata, "file_outside_workspace"),
        (tool, {"ref": "r1", "files": ["output"]}, metadata, "invalid_args"),
        (small, {"ref": "r1", "files": ["output/report.pdf"]}, metadata, "file_too_large"),
        (tool, {"ref": "r1", "files": [f"output/{i}.txt" for i in range(11)]}, metadata, "too_many_files"),
        (tool, {"files": ["output/report.pdf"]}, metadata, "invalid_args"),
        (tool, {"ref": "r1"}, metadata, "invalid_args"),
        (tool, {"ref": "r1", "files": "not-a-list-of-paths-but-ok"}, metadata, "file_not_found"),
        (tool, {"ref": "r1", "files": [{"path": "output/report.pdf"}]}, metadata, "invalid_args"),
        (tool, {"ref": "r1", "files": ["output/report.pdf"]}, dict(ENABLED_METADATA), "no_workspace"),
    ]
    if has_symlink:
        cases.append((tool, {"ref": "r1", "files": ["output/escape.txt"]}, metadata, "file_outside_workspace"))
    for subject, params, meta, code in cases:
        result = await subject.execute({"action": "page.upload", "params": params}, _context(meta))
        assert result.success is False, params
        assert result.error == code, (params, result.content)
        assert result.output["error"]["code"] == code
    assert broker.pending("session-browser") == []
    assert events == []


# ---------------------------------------------------------------------------
# workflow.run


def _skills(tmp_path: Path) -> SkillDiscovery:
    skill = tmp_path / "skills" / "productivity-suite-analyze-file"
    skill.mkdir(parents=True)
    (skill / "skill.md").write_text(
        "---\nname: productivity-suite-analyze-file\ndescription: \"Analyze a file. Use when: asked.\"\n---\n"
        "Run workflow.yaml.\n",
        encoding="utf-8",
    )
    (skill / "workflow.yaml").write_text(PLAYBOOK, encoding="utf-8")
    return SkillDiscovery([tmp_path / "skills"])


@pytest.mark.asyncio
async def test_workflow_run_reads_the_playbook_from_the_skill_and_names_files(tmp_path: Path):
    workspace, metadata = _workspace(tmp_path)
    (workspace / "uploads").mkdir()
    (workspace / "uploads" / "contract.pdf").write_bytes(b"x" * 10)
    broker = ConnectorBridgeBroker()
    tool = create_browser_tool(broker, skill_discovery=_skills(tmp_path))
    request, task = await _run(
        tool,
        broker,
        {
            "action": "workflow.run",
            "params": {
                "workflow": "productivity-suite-analyze-file/workflow.yaml",
                "vars": {"base_url": "https://ps.example.test", "instruction": "Summarize", "count": 3},
                "files": {"file": "uploads/contract.pdf"},
                "yes": True,
            },
        },
        metadata,
    )
    assert request.params["workflow_yaml"] == PLAYBOOK
    assert request.params["workflow"] == "productivity-suite-analyze-file/workflow.yaml"
    assert request.params["vars"] == {"base_url": "https://ps.example.test", "instruction": "Summarize", "count": "3"}
    assert request.params["files"] == [{"var": "file", "path": "uploads/contract.pdf", "name": "contract.pdf", "bytes": 10}]
    assert request.params["yes"] is True and request.params["dry_run"] is False
    # Playbooks get a longer default wait than a single page action.
    assert request.timeout_seconds == 90

    broker.resolve(
        request.request_id,
        {
            "ok": True,
            "result": {
                "pass": True,
                "status": "passed",
                "step_count": 3,
                "completed_steps": 3,
                "duration_ms": 1234,
                "steps": [
                    {"index": 0, "action": "tab.open", "status": "passed", "duration_ms": 100},
                    {"index": 1, "action": "page.upload", "status": "passed", "duration_ms": 200},
                    {
                        "index": 2,
                        "action": "page.extract_schema",
                        "status": "passed",
                        "duration_ms": 300,
                        "data": {
                            "result": {
                                "fields": [
                                    {"name": "result", "value": "All good"},
                                    {"name": "links", "values": ["a", "b"]},
                                    {"name": "missing_one", "missing": True},
                                ]
                            },
                            "smart_wait": {"waited_ms": 5},
                        },
                    },
                ],
            },
        },
    )
    result = await task
    assert result.success is True
    assert result.content.startswith("browser workflow.run passed: 3/3 steps in 1234 ms")
    assert "#2 page.extract_schema passed 300 ms" in result.content
    assert "<page-content>\nresult: All good\nlinks: a | b\nmissing_one: (missing)\n</page-content>" in result.content
    assert result.output["data"]["pass"] is True


@pytest.mark.asyncio
async def test_workflow_run_prefers_a_workspace_copy_and_accepts_yaml_text(tmp_path: Path):
    workspace, metadata = _workspace(tmp_path)
    (workspace / "playbooks").mkdir()
    (workspace / "playbooks" / "local.yaml").write_text("steps:\n  - action: page.wait\n    selector: .ready\n")
    broker = ConnectorBridgeBroker()
    tool = create_browser_tool(broker, skill_discovery=_skills(tmp_path))

    request, task = await _run(
        tool, broker, {"action": "workflow.run", "params": {"workflow": "playbooks/local.yaml"}}, metadata
    )
    assert request.params["workflow_yaml"].startswith("steps:")
    assert request.params["vars"] == {} and request.params["files"] == []
    broker.resolve(request.request_id, {"ok": True, "result": {"pass": True, "status": "passed", "steps": []}})
    await task

    request, task = await _run(
        tool,
        broker,
        {"action": "workflow.run", "params": {"workflow_yaml": "steps: [{action: page.wait, selector: x}]", "dry_run": True}},
        dict(ENABLED_METADATA),
    )
    assert request.params["workflow_yaml"] == "steps: [{action: page.wait, selector: x}]"
    assert request.params["dry_run"] is True and "workflow" not in request.params
    broker.resolve(request.request_id, {"ok": True, "result": {"dry_run": True, "status": "planned", "steps": []}})
    result = await task
    assert result.content.startswith("browser workflow.run planned")


@pytest.mark.asyncio
async def test_workflow_run_rejects_bad_params(tmp_path: Path):
    workspace, metadata = _workspace(tmp_path)
    broker = ConnectorBridgeBroker()
    tool = create_browser_tool(broker, skill_discovery=_skills(tmp_path))
    cases = [
        ({}, "invalid_args"),
        ({"workflow": "nope/workflow.yaml"}, "workflow_not_found"),
        ({"workflow": "productivity-suite-analyze-file/../other.yaml"}, "workflow_not_found"),
        ({"workflow": "/etc/passwd"}, "invalid_args"),
        ({"workflow_yaml": "#" * (256 * 1024 + 1)}, "workflow_too_large"),
        ({"workflow_yaml": "steps: []", "vars": {"nested": {"a": 1}}}, "invalid_args"),
        ({"workflow_yaml": "steps: []", "vars": ["a"]}, "invalid_args"),
        ({"workflow_yaml": "steps: []", "files": ["uploads/a.pdf"]}, "invalid_args"),
        ({"workflow_yaml": "steps: []", "files": {"file": "uploads/missing.pdf"}}, "file_not_found"),
        ({"workflow_yaml": "steps: []", "files": {"bad name": "output/report.pdf"}}, "invalid_args"),
    ]
    for params, code in cases:
        result = await tool.execute({"action": "workflow.run", "params": params}, _context(metadata))
        assert result.success is False and result.error == code, (params, result.content)
    assert broker.pending("session-browser") == []


@pytest.mark.asyncio
async def test_workflow_failure_renders_the_failing_step(tmp_path: Path):
    broker = ConnectorBridgeBroker()
    tool = create_browser_tool(broker)
    request, task = await _run(
        tool,
        broker,
        {"action": "workflow.run", "params": {"workflow_yaml": "steps: [{action: page.click, selector: button}]"}},
        dict(ENABLED_METADATA),
    )
    broker.resolve(
        request.request_id,
        {
            "ok": False,
            "error": {
                "code": "workflow_failed",
                "message": "Browser workflow failed.",
                "hint": "Inspect data.steps for the first failing whitelisted step.",
                "data": {
                    "pass": False,
                    "status": "failed",
                    "failed_step": 1,
                    "step_count": 3,
                    "completed_steps": 2,
                    "duration_ms": 900,
                    "steps": [
                        {"index": 0, "action": "tab.open", "status": "passed", "duration_ms": 100},
                        {
                            "index": 1,
                            "action": "page.click",
                            "status": "failed",
                            "duration_ms": 800,
                            "error": {"code": "selector_not_found", "message": "no Analyze button", "hint": "Run page.ax"},
                        },
                    ],
                },
            },
        },
    )
    result = await task
    assert result.success is False
    assert result.error == "workflow_failed"
    assert "browser workflow.run failed: 2/3 steps in 900 ms" in result.content
    assert "#1 page.click failed 800 ms - selector_not_found: no Analyze button (hint: Run page.ax)" in result.content
    assert result.output["data"]["failed_step"] == 1
