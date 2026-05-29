from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.agents.profile import AgentProfile
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.permissions import (
    ALLOW,
    ASK,
    ConfiguredPermissionBroker,
    PermissionConfig,
    PermissionMetadata,
    PermissionRequest,
    normalize_agent_permission_overlay,
    normalize_tool_permissions,
)
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.runtime.agent import _resolve_config
from efp_runtime.tools.builtin.task import TaskToolRequest, create_task_tool
from efp_runtime.tools.definition import ToolContext, ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


def test_normalize_tool_permissions_accepts_nested_subject_maps():
    normalized = normalize_tool_permissions(
        {
            "skill": {
                "*": "allow",
                "internal-*": "deny",
                "experimental-*": {
                    "action": "ask",
                    "reason": "Review experimental skill.",
                    "risk": "medium",
                },
            }
        }
    )

    assert normalized == {
        "skill": {
            "*": "allow",
            "internal-*": "deny",
            "experimental-*": {
                "action": "ask",
                "reason": "Review experimental skill.",
                "risk": "medium",
            },
        }
    }
    assert [
        (rule.key, rule.subject_pattern, rule.action)
        for rule in PermissionConfig(normalized).rules
    ] == [
        ("skill", "*", "allow"),
        ("skill", "internal-*", "deny"),
        ("skill", "experimental-*", "ask"),
    ]


@pytest.mark.parametrize(
    ("permissions", "error"),
    [
        ({"skill": {1: "deny"}}, "subject patterns must be strings"),
        ({"skill": {"": "deny"}}, "subject patterns must not be empty"),
        ({"skill": {"internal-*": 7}}, "nested permission values"),
        ({"skill": {"internal-*": {"reason": "missing action"}}}, "requires an action"),
        (
            {"skill": {"internal-*": {"action": "ask", "patterns": ["x"]}}},
            "unsupported key",
        ),
    ],
)
def test_normalize_tool_permissions_rejects_malformed_nested_entries(
    permissions: dict[str, Any],
    error: str,
):
    with pytest.raises((TypeError, ValueError), match=error):
        normalize_tool_permissions(permissions)


@pytest.mark.asyncio
async def test_bash_allow_config_executes_shell_without_pending(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    _provider_tool_call(
                        "call-shell",
                        "bash",
                        {"command": "printf ok", "description": "Print ok"},
                    )
                ]
            },
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            tool_permissions={"bash": "allow"},
        ),
    )

    result = await runtime.run("Run shell.", session_id="session-bash-allow")

    assert result.status == LoopStatus.COMPLETED
    assert runtime.pending_permissions() == []
    history = runtime.store.read_history("session-bash-allow")
    tool_result = history[2].parts[0].tool_result
    assert tool_result.status == "success"
    assert tool_result.output["stdout"] == "ok"


def test_shell_permission_request_respects_static_patterns():
    request = PermissionRequest.create(
        tool_id="bash",
        args={"command": "cat src/app.py", "description": "Read app"},
        metadata=PermissionMetadata(
            action=ASK,
            category="shell",
            data={"patterns": ["configured-shell-pattern"]},
        ),
    )

    assert request.patterns == ["configured-shell-pattern"]
    assert request.metadata["patterns"] == ["configured-shell-pattern"]
    assert request.metadata["permission_patterns"] == ["configured-shell-pattern"]
    assert request.metadata["path_args"][0]["path"] == "src/app.py"


@pytest.mark.asyncio
async def test_edit_deny_config_rejects_write_edit_and_apply_patch(tmp_path: Path):
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(
            workspace_root=tmp_path,
            tool_permissions={"edit": "deny"},
        ),
    )

    results = [
        await _execute_tool(
            runtime,
            "write",
            {"filePath": "created.txt", "content": "blocked"},
        ),
        await _execute_tool(
            runtime,
            "edit",
            {"filePath": "created.txt", "oldString": "a", "newString": "b"},
        ),
        await _execute_tool(runtime, "apply_patch", {"patch": ""}),
    ]

    assert [result.status for result in results] == [
        "permission_denied",
        "permission_denied",
        "permission_denied",
    ]
    assert {result.error for result in results} == {
        "Permission denied by runtime config: edit"
    }
    assert (tmp_path / "created.txt").exists() is False


@pytest.mark.asyncio
async def test_exact_tool_permission_precedes_category_alias(tmp_path: Path):
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(
            workspace_root=tmp_path,
            tool_permissions={"edit": "deny", "write": "allow"},
        ),
    )

    write_result = await _execute_tool(
        runtime,
        "write",
        {"filePath": "allowed.txt", "content": "allowed"},
    )
    edit_result = await _execute_tool(
        runtime,
        "edit",
        {"filePath": "allowed.txt", "oldString": "allowed", "newString": "blocked"},
    )
    patch_result = await _execute_tool(runtime, "apply_patch", {"patch": ""})

    assert write_result.status == "success"
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "allowed"
    assert edit_result.status == "permission_denied"
    assert patch_result.status == "permission_denied"


@pytest.mark.asyncio
async def test_wildcard_permission_config_can_ask_for_custom_tools():
    called: list[dict[str, Any]] = []
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(
            tool_permissions={
                "external_*": {
                    "action": "ask",
                    "reason": "Review external tool call.",
                    "risk": "high",
                    "patterns": ["external:*"],
                }
            },
        ),
        tool_registry=ToolRegistry([_custom_tool("external_alpha", called)]),
    )

    result = await _execute_tool(runtime, "external_alpha", {"value": "one"})
    request = result.metadata["permission_request"]

    assert called == []
    assert result.status == "permission_requested"
    assert request["tool_id"] == "external_alpha"
    assert request["reason"] == "Review external tool call."
    assert request["risk"] == "high"
    assert request["patterns"] == ["external:*"]
    assert request["metadata"]["patterns"] == ["external:*"]
    assert request["metadata"]["permission_config_key"] == "external_*"
    assert request["metadata"]["permission_config_match"] == "wildcard"
    assert runtime.pending_permissions()[0]["request_id"] == request["request_id"]


@pytest.mark.asyncio
async def test_skill_nested_permission_config_matches_skill_name(tmp_path: Path):
    _write_skill(tmp_path, "internal-docs")
    _write_skill(tmp_path, "public-docs")
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[tmp_path],
            tool_permissions={
                "skill": {
                    "*": "allow",
                    "internal-*": "deny",
                }
            },
        ),
    )

    denied = await _execute_tool(runtime, "skill", {"name": "internal-docs"})
    allowed = await _execute_tool(runtime, "skill", {"name": "public-docs"})

    assert denied.status == "permission_denied"
    assert denied.error == "Permission denied by runtime config: skill"
    assert allowed.status == "success"
    assert allowed.output["name"] == "public-docs"


@pytest.mark.asyncio
async def test_skill_nested_ask_request_patterns_use_actual_skill_name(
    tmp_path: Path,
):
    _write_skill(tmp_path, "experimental-docs")
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[tmp_path],
            tool_permissions={
                "skill": {
                    "*": "allow",
                    "experimental-*": {
                        "action": "ask",
                        "reason": "Review experimental skill.",
                    },
                }
            },
        ),
    )

    result = await _execute_tool(runtime, "skill", {"name": "experimental-docs"})
    request = result.metadata["permission_request"]

    assert result.status == "permission_requested"
    assert request["patterns"] == ["experimental-docs"]
    assert request["metadata"]["permission_config_key"] == "skill"
    assert request["metadata"]["permission_config_match"] == "exact_subject"
    assert request["metadata"]["permission_config_subject_pattern"] == "experimental-*"
    assert runtime.pending_permissions()[0]["request_id"] == request["request_id"]


@pytest.mark.asyncio
async def test_task_nested_permission_config_matches_subagent_type():
    called: list[str] = []

    async def runner(request: TaskToolRequest) -> str:
        called.append(request.subagent_type)
        return "ok"

    runtime = ToolRuntime(
        ToolRegistry([create_task_tool(runner)]),
        permission_evaluator=ConfiguredPermissionBroker(
            {
                "task": {
                    "*": "allow",
                    "dangerous-reviewer": "deny",
                }
            }
        ),
    )

    denied = await runtime.execute(
        ToolCall(
            id="call-task-denied",
            tool_id="task",
            args={
                "description": "Review danger",
                "prompt": "Inspect.",
                "subagent_type": "dangerous-reviewer",
            },
        )
    )
    allowed = await runtime.execute(
        ToolCall(
            id="call-task-allowed",
            tool_id="task",
            args={
                "description": "Review safely",
                "prompt": "Inspect.",
                "subagent_type": "scout",
            },
        )
    )

    assert denied.status == "permission_denied"
    assert allowed.status == "success"
    assert called == ["scout"]


@pytest.mark.asyncio
async def test_more_specific_wildcard_permission_wins_over_earlier_match():
    called: list[dict[str, Any]] = []
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(
            tool_permissions={
                "external_*": "deny",
                "external_alpha*": "ask",
            },
        ),
        tool_registry=ToolRegistry([_custom_tool("external_alpha", called)]),
    )

    result = await _execute_tool(runtime, "external_alpha", {"value": "one"})

    assert result.status == "permission_requested"
    assert called == []
    assert result.metadata["permission_request"]["metadata"][
        "permission_config_key"
    ] == "external_alpha*"


@pytest.mark.asyncio
async def test_agent_permission_overlay_precedes_base_exact_category_and_wildcard():
    broker = ConfiguredPermissionBroker(
        {
            "alpha": "allow",
            "write": "allow",
            "external_*": "allow",
        }
    )
    context = ToolContext(
        session_id="session-overlay",
        metadata={
            "agent_permission_overlay": {
                "alpha": "deny",
                "edit": "deny",
                "external_*": "deny",
            },
            "agent_permission_overlay_source": "agent_profile",
        },
    )

    exact = await broker.evaluate(
        tool_id="alpha",
        args={},
        metadata=PermissionMetadata(action=ALLOW),
        context=context,
    )
    category = await broker.evaluate(
        tool_id="write",
        args={},
        metadata=PermissionMetadata(action=ALLOW, category="filesystem"),
        context=context,
    )
    wildcard = await broker.evaluate(
        tool_id="external_beta",
        args={},
        metadata=PermissionMetadata(action=ALLOW, category="external"),
        context=context,
    )

    assert exact.action == "deny"
    assert exact.reason == "Permission denied by agent permission overlay: alpha"
    assert category.action == "deny"
    assert category.reason == "Permission denied by agent permission overlay: edit"
    assert wildcard.action == "deny"
    assert wildcard.reason == (
        "Permission denied by agent permission overlay: external_*"
    )


@pytest.mark.asyncio
async def test_agent_permission_overlay_accepts_nested_subject_maps():
    overlay = {
        "permission": {
            "skill": {
                "*": "allow",
                "internal-*": "deny",
            }
        }
    }
    assert normalize_agent_permission_overlay(overlay) == overlay["permission"]
    broker = ConfiguredPermissionBroker({"skill": {"*": "allow"}})
    context = ToolContext(
        session_id="session-overlay-subject",
        metadata={
            "agent_permission_overlay": overlay["permission"],
            "agent_permission_overlay_source": "agent_profile",
        },
    )
    metadata = PermissionMetadata(
        action=ALLOW,
        category="skill",
        data={"subject_arg": "name"},
    )

    denied = await broker.evaluate(
        tool_id="skill",
        args={"name": "internal-docs"},
        metadata=metadata,
        context=context,
    )
    allowed = await broker.evaluate(
        tool_id="skill",
        args={"name": "public-docs"},
        metadata=metadata,
        context=context,
    )

    assert denied.action == "deny"
    assert denied.reason == "Permission denied by agent permission overlay: skill"
    assert allowed.action == "allow"


@pytest.mark.asyncio
async def test_configured_ask_approve_once_then_resume_executes_pending_call(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    _provider_tool_call(
                        "call-write",
                        "write",
                        {"filePath": "approved.txt", "content": "approved\n"},
                    )
                ]
            },
            {"content": "written"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            model_aware_tool_selection=False,
            tool_permissions={
                "edit": {
                    "action": "ask",
                    "reason": "Configured edit approval.",
                }
            },
        ),
    )

    first = await runtime.run("Write.", session_id="session-config-ask")
    request_id = first.pending_permission_request["request_id"]

    assert first.status == LoopStatus.WAITING_FOR_PERMISSION
    assert first.pending_permission_request["reason"] == "Configured edit approval."

    runtime.approve_permission(request_id, always=False)
    resumed = await runtime.resume("session-config-ask")

    assert resumed.status == LoopStatus.COMPLETED
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "approved\n"
    assert runtime.pending_permissions() == []


def test_resolve_config_and_child_config_preserve_tool_permissions(tmp_path: Path):
    base = RuntimeConfig(
        workspace_root=tmp_path,
        tool_permissions={
            "edit": {
                "action": "ask",
                "reason": "Review edits.",
                "patterns": ["*.py"],
            },
            "bash": "deny",
        },
        metadata={"base": True},
    )

    resolved = _resolve_config(
        base,
        workspace_root=None,
        max_iterations=None,
        max_context_parts=None,
        max_context_chars=None,
        context_reserve_chars=None,
        metadata={"run": True},
    )
    child = _child_config(
        profile=AgentProfile(name="general"),
        base_config=base,
        workspace_root=None,
        metadata={"child": True},
    )

    assert resolved.tool_permissions == base.tool_permissions
    assert resolved.tool_permissions is not base.tool_permissions
    assert resolved.tool_permissions["edit"] is not base.tool_permissions["edit"]
    assert resolved.metadata == {"base": True, "run": True}
    assert child.tool_permissions == base.tool_permissions
    assert child.tool_permissions is not base.tool_permissions
    assert child.tool_permissions["edit"] is not base.tool_permissions["edit"]
    assert child.metadata["child"] is True


@pytest.mark.asyncio
async def test_permission_config_does_not_control_tool_schema_visibility(
    tmp_path: Path,
):
    visible_provider = ScriptedLLMProvider([{"content": "done"}])
    visible_runtime = AgentRuntime(
        provider=visible_provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
                model_aware_tool_selection=False,
                tool_permissions={"edit": "deny"},
        ),
    )

    visible_result = await visible_runtime.run(
        "List tools.",
        session_id="session-visible-tools",
    )
    denied = await _execute_tool(
        visible_runtime,
        "edit",
        {"filePath": "file.txt", "oldString": "a", "newString": "b"},
    )

    visible_tool_ids = [
        schema.id for schema in visible_provider.requests[0].provider_request.tools
    ]
    assert visible_result.status == LoopStatus.COMPLETED
    assert "apply_patch" in visible_tool_ids
    assert denied.status == "permission_denied"

    disabled_provider = ScriptedLLMProvider([{"content": "done"}])
    disabled_runtime = AgentRuntime(
        provider=disabled_provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            model_aware_tool_selection=False,
            disabled_tools=["edit"],
            tool_permissions={"edit": "deny"},
        ),
    )

    disabled_result = await disabled_runtime.run(
        "List tools.",
        session_id="session-disabled-edit",
    )
    disabled_tool_ids = [
        schema.id for schema in disabled_provider.requests[0].provider_request.tools
    ]

    assert disabled_result.status == LoopStatus.COMPLETED
    assert "edit" not in disabled_tool_ids
    assert "write" in disabled_tool_ids


def test_permission_config_import_boundary():
    code = """
import json
import sys

from efp_runtime.permissions import ConfiguredPermissionBroker, PermissionMetadata

broker = ConfiguredPermissionBroker({"bash": "allow"})
match = broker.permission_config.match(
    tool_id="bash",
    metadata=PermissionMetadata(category="shell"),
)
legacy_modules = [
    "src.sessions",
    "src.agents.core",
    "src.runtime",
    "src.skills",
]
print(json.dumps({
    "matched": match.rule.key if match else None,
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
    assert payload == {"matched": "bash", "legacy_loaded": []}


async def _execute_tool(
    runtime: AgentRuntime,
    tool_id: str,
    args: dict[str, Any],
):
    return await runtime.tool_runtime.execute(
        ToolCall(id=f"call-{tool_id}", tool_id=tool_id, args=args),
        context=ToolContext(session_id=f"session-{tool_id}"),
    )


def _provider_tool_call(
    call_id: str,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(args),
        },
    }


def _custom_tool(tool_id: str, called: list[dict[str, Any]]) -> ToolDef:
    async def execute(args: dict[str, Any], context: ToolContext) -> str:
        called.append({"args": args, "session_id": context.session_id})
        return "executed"

    return ToolDef(
        id=tool_id,
        description=f"{tool_id} tool",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="external",
            resource="custom",
            risk="low",
        ),
    )


def _write_skill(tmp_path: Path, name: str) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir
