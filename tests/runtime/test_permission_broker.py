from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import ASK, PermissionBroker, PermissionMetadata
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext, ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_builtin_ask_requests_are_recorded_as_pending(tmp_path: Path):
    runtime = ToolRuntime(_ask_core_registry(tmp_path))
    broker = runtime.permission_evaluator
    assert isinstance(broker, PermissionBroker)
    context = ToolContext(session_id="session-permissions")

    write_result = await runtime.execute(
        ToolCall(
            id="call-write",
            tool_id="write",
            args={"filePath": "created.txt", "content": "blocked"},
        ),
        context=context,
    )
    shell_result = await runtime.execute(
        ToolCall(id="call-shell", tool_id="bash", args={"command": "printf ok", "description": "Print ok"}),
        context=context,
    )

    assert write_result.status == "permission_requested"
    assert shell_result.status == "permission_requested"

    write_request = write_result.metadata["permission_request"]
    shell_request = shell_result.metadata["permission_request"]
    assert write_request["request_id"].startswith("perm_")
    assert shell_request["request_id"].startswith("perm_")
    assert write_request["tool_id"] == "write"
    assert write_request["action"] == "ask"
    assert write_request["category"] == "filesystem"
    assert write_request["session_id"] == "session-permissions"

    pending_ids = [request.request_id for request in broker.pending()]
    assert pending_ids == [write_request["request_id"], shell_request["request_id"]]
    assert broker.get(write_request["request_id"]).to_dict() == write_request


@pytest.mark.asyncio
async def test_approve_once_allows_the_next_matching_retry_only(tmp_path: Path):
    broker = PermissionBroker()
    runtime = ToolRuntime(
        _ask_core_registry(tmp_path),
        permission_evaluator=broker,
    )
    context = ToolContext(session_id="session-once")
    args = {"filePath": "notes/result.txt", "content": "approved\n"}

    first = await runtime.execute(
        ToolCall(id="call-write-ask", tool_id="write", args=args),
        context=context,
    )
    request_id = first.metadata["permission_request"]["request_id"]

    decision = broker.approve(request_id, always=False)
    retry = await runtime.execute(
        ToolCall(id="call-write-retry", tool_id="write", args=args),
        context=context,
    )
    third = await runtime.execute(
        ToolCall(id="call-write-third", tool_id="write", args=args),
        context=context,
    )

    assert decision.action == "allow"
    assert retry.status == "success"
    assert (tmp_path / "notes/result.txt").read_text(encoding="utf-8") == "approved\n"
    assert third.status == "permission_requested"
    assert third.metadata["permission_request"]["request_id"] == request_id


@pytest.mark.asyncio
async def test_approve_always_allows_subsequent_same_tool_and_category(tmp_path: Path):
    broker = PermissionBroker()
    runtime = ToolRuntime(
        _ask_core_registry(tmp_path),
        permission_evaluator=broker,
    )
    context = ToolContext(session_id="session-always")

    first = await runtime.execute(
        ToolCall(
            id="call-write-ask",
            tool_id="write",
            args={"filePath": "first.txt", "content": "first"},
        ),
        context=context,
    )
    broker.approve(first.metadata["permission_request"]["request_id"], always=True)

    second = await runtime.execute(
        ToolCall(
            id="call-write-allow",
            tool_id="write",
            args={"filePath": "second.txt", "content": "second"},
        ),
        context=context,
    )

    assert second.status == "success"
    assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "second"
    assert broker.pending() == []


@pytest.mark.asyncio
async def test_deny_always_denies_subsequent_same_tool_and_category(tmp_path: Path):
    broker = PermissionBroker()
    runtime = ToolRuntime(
        _ask_core_registry(tmp_path),
        permission_evaluator=broker,
    )
    context = ToolContext(session_id="session-deny")

    first = await runtime.execute(
        ToolCall(
            id="call-write-ask",
            tool_id="write",
            args={"filePath": "blocked.txt", "content": "blocked"},
        ),
        context=context,
    )
    broker.deny(
        first.metadata["permission_request"]["request_id"],
        always=True,
        reason="No writes in this session.",
    )

    denied = await runtime.execute(
        ToolCall(
            id="call-write-denied",
            tool_id="write",
            args={"filePath": "other.txt", "content": "blocked"},
        ),
        context=context,
    )

    assert denied.status == "permission_denied"
    assert denied.error == "No writes in this session."
    assert (tmp_path / "other.txt").exists() is False


@pytest.mark.asyncio
async def test_permission_request_metadata_is_json_serializable(tmp_path: Path):
    async def execute(args, context):
        return "should not run"

    broker = PermissionBroker()
    runtime = ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id="needs_permission",
                    description="Needs permission",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                    permission=PermissionMetadata(
                        action=ASK,
                        reason="Approval required.",
                        category="custom",
                        data={"path": tmp_path},
                    ),
                )
            ]
        ),
        permission_evaluator=broker,
    )

    result = await runtime.execute(
        ToolCall(id="call-custom", tool_id="needs_permission", args={}),
        context=ToolContext(session_id="session-json"),
    )
    request = result.metadata["permission_request"]

    encoded = json.dumps(request, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded["request_id"] == request["request_id"]
    assert decoded["metadata"]["path"] == str(tmp_path)


def test_permission_broker_import_boundary():
    code = """
import json
import sys

from efp_runtime.permissions import PermissionBroker, PermissionRule

broker = PermissionBroker()
rule = PermissionRule(tool_id="write", action="allow")
broker.add_rule(rule)
legacy_modules = [
    "src.agents.core",
    "src.bash_tools",
    "src.github",
    "src.jira",
    "src.confluence",
    "src.git",
    "src.context_tools",
]
print(json.dumps({
    "pending": broker.pending(),
    "rules": [item.to_dict() for item in broker.rules],
    "legacy_loaded": [name for name in legacy_modules if name in sys.modules],
}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "pending": [],
        "rules": [
            {
                "tool_id": "write",
                "category": None,
                "action": "allow",
                "patterns": [],
                "scope": "always",
                "reason": "",
                "metadata": {},
            }
        ],
        "legacy_loaded": [],
    }


def test_permission_broker_source_stays_inside_runtime_boundary():
    source = (ROOT / "src/efp_runtime/permissions.py").read_text(encoding="utf-8")
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "src.agents.core",
        "Agent.process(",
        "SkillSession(",
        "SkillsExecutor(",
    ]

    for token in forbidden_tokens:
        assert token not in source


def _ask_core_registry(workspace_root: Path):
    ask_write = PermissionMetadata(
        action=ASK,
        category="filesystem",
        resource="workspace",
        risk="medium",
    )
    ask_shell = PermissionMetadata(
        action=ASK,
        category="shell",
        resource="workspace",
        risk="high",
    )
    return create_core_tool_registry(
        workspace_root,
        write_permission=ask_write,
        shell_permission=ask_shell,
    )
