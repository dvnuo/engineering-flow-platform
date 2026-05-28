from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.agents import (
    AgentProfile,
    AgentRegistry,
    create_agent_task_tool,
    create_agent_task_tools,
    create_subagent_task_runner,
)
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.loop import (
    LoopStatus,
    RuntimeLoopRunner,
    RuntimeRequest,
    ScriptedLLMProvider,
)
from efp_runtime.models import ToolCall
from efp_runtime.runtime import RuntimeConfig
from efp_runtime.session.models import MessagePartType, MessageRole
from efp_runtime.tools.builtin.task import TaskToolRequest, create_task_tool
from efp_runtime.tools.definition import ToolContext, ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


def test_agent_profile_and_registry_resolution():
    source_tools = {"alpha": True}
    source_skills = [" review-pr ", "review-pr"]
    source_metadata = {"kind": "debug"}
    profile = AgentProfile(
        name=" debugger ",
        prompt="Inspect failures.",
        tools=source_tools,
        active_skills=source_skills,
        max_iterations=2,
        metadata=source_metadata,
    )
    source_tools["alpha"] = False
    source_skills.append("other")
    source_metadata["kind"] = "changed"

    assert profile.name == "debugger"
    assert profile.tools == {"alpha": True}
    assert profile.active_skills == ["review-pr"]
    assert profile.metadata == {"kind": "debug"}

    registry = AgentRegistry(
        [
            AgentProfile(name="general", description="General purpose"),
            profile,
        ],
        default_agent="general",
    )
    assert registry.get("debugger") is profile
    assert registry.resolve("debugger") is profile
    assert registry.resolve("missing").name == "general"

    mapped = AgentRegistry.from_mappings(
        {
            "general": {"prompt": "Default instructions."},
            "reviewer": {"description": "Reviews code."},
        }
    )
    assert mapped.resolve("reviewer").description == "Reviews code."
    assert mapped.resolve("unknown").name == "general"

    strict = AgentRegistry([AgentProfile(name="debugger")], default_agent="general")
    with pytest.raises(KeyError) as error:
        strict.resolve("missing")
    message = str(error.value)
    assert "missing" in message
    assert "debugger" in message

    with pytest.raises(ValueError, match="name"):
        AgentProfile(name="")
    with pytest.raises(ValueError, match="max_iterations"):
        AgentProfile(name="bad", max_iterations=0)


def test_agent_registry_profiles_returns_stable_sorted_profiles():
    beta = AgentProfile(name="beta")
    alpha = AgentProfile(name="alpha")
    registry = AgentRegistry([beta, alpha], default_agent=None)

    assert registry.profiles() == [alpha, beta]
    assert registry.profiles()[0] is alpha
    assert registry.profiles()[1] is beta


def test_create_agent_task_tool_description_lists_custom_profiles():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tool = create_agent_task_tool(
        provider=provider,
        profiles=[
            AgentProfile(
                name="general",
                description="General Runtime v2 work.",
            ),
            AgentProfile(
                name="debugger",
                description="Debug failing tests.",
                metadata={"mode": "subagent"},
            ),
            AgentProfile(
                name="reviewer",
                description="Review code changes.",
                metadata={"mode": "all"},
            ),
        ],
    )

    assert tool.description == "\n".join(
        [
            "Delegate a task to an injected Runtime v2 task runner.",
            "",
            "Available agent types:",
            "- debugger: Debug failing tests.",
            "- general: General Runtime v2 work.",
            "- reviewer: Review code changes.",
        ]
    )


def test_agent_task_tool_description_hides_denied_subagent_profiles():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tool = create_agent_task_tool(
        provider=provider,
        profiles=[
            AgentProfile(name="general", description="General Runtime v2 work."),
            AgentProfile(name="debugger", description="Debug failing tests."),
            AgentProfile(name="reviewer", description="Review code changes."),
        ],
        base_config=RuntimeConfig(
            tool_permissions={"task": {"debugger": "deny", "*": "allow"}},
        ),
    )

    assert "- debugger:" not in tool.description
    assert "- general: General Runtime v2 work." in tool.description
    assert "- reviewer: Review code changes." in tool.description


def test_agent_task_tool_description_keeps_ask_subagent_profiles_visible():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tool = create_agent_task_tool(
        provider=provider,
        profiles=[
            AgentProfile(name="debugger", description="Debug failing tests."),
            AgentProfile(name="reviewer", description="Review code changes."),
        ],
        base_config=RuntimeConfig(
            tool_permissions={"task": {"reviewer": "ask", "*": "allow"}},
        ),
    )

    assert "- debugger: Debug failing tests." in tool.description
    assert "- reviewer: Review code changes." in tool.description


def test_agent_task_tool_description_reports_no_permission_visible_subagents():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tool = create_agent_task_tool(
        provider=provider,
        profiles=[
            AgentProfile(name="debugger", description="Debug failing tests."),
            AgentProfile(name="reviewer", description="Review code changes."),
        ],
        base_config=RuntimeConfig(tool_permissions={"task": {"*": "deny"}}),
    )

    assert tool.description == "\n".join(
        [
            "Delegate a task to an injected Runtime v2 task runner.",
            "",
            "Available agent types:",
            "No subagents are available.",
        ]
    )


def test_agent_task_tool_description_filters_primary_profiles_and_empty_text():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tool = create_agent_task_tool(
        provider=provider,
        profiles=[
            AgentProfile(name="build", description="Build work."),
            AgentProfile(name="plan", description="Plan work."),
            AgentProfile(
                name="builder",
                description="Build mode.",
                metadata={"mode": "build"},
            ),
            AgentProfile(
                name="hidden",
                description="Hidden.",
                metadata={"hidden": True},
            ),
            AgentProfile(name="manual", metadata={"mode": "subagent"}),
            AgentProfile(
                name="primary",
                description="Primary.",
                metadata={"mode": "primary"},
            ),
            AgentProfile(
                name="scout",
                description="Search quickly.",
                metadata={"mode": "scout"},
            ),
        ],
    )

    assert "- manual: This subagent should only be called manually by the user." in (
        tool.description
    )
    assert "- scout: Search quickly." in tool.description
    assert "- build:" not in tool.description
    assert "- plan:" not in tool.description
    assert "- builder:" not in tool.description
    assert "- hidden:" not in tool.description
    assert "- primary:" not in tool.description


def test_agent_task_tool_without_base_config_preserves_visible_profiles():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tool = create_agent_task_tool(
        provider=provider,
        profiles=[
            AgentProfile(name="debugger", description="Debug failing tests."),
            AgentProfile(name="reviewer", description="Review code changes."),
        ],
    )

    assert tool.description == "\n".join(
        [
            "Delegate a task to an injected Runtime v2 task runner.",
            "",
            "Available agent types:",
            "- debugger: Debug failing tests.",
            "- reviewer: Review code changes.",
        ]
    )


def test_agent_task_tool_description_reports_no_available_subagents():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tool = create_agent_task_tool(
        provider=provider,
        profiles=[
            AgentProfile(name="plan", description="Plan work."),
            AgentProfile(name="build", description="Build work."),
            AgentProfile(name="hidden", metadata={"hidden": True}),
        ],
    )

    assert tool.description == "\n".join(
        [
            "Delegate a task to an injected Runtime v2 task runner.",
            "",
            "Available agent types:",
            "No subagents are available.",
        ]
    )


def test_create_agent_task_tools_background_keeps_task_description_consistent():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tools = create_agent_task_tools(
        provider=provider,
        profiles=[
            AgentProfile(name="general", description="General work."),
            AgentProfile(name="debugger", description="Debug failures."),
        ],
        allow_background=True,
    )

    assert [tool.id for tool in tools] == ["task", "task_status", "task_cancel"]
    assert tools[0].description == "\n".join(
        [
            "Delegate a task to an injected Runtime v2 task runner.",
            "",
            "Available agent types:",
            "- debugger: Debug failures.",
            "- general: General work.",
        ]
    )
    assert (
        tools[1].description
        == "Read status and results from background subagent tasks."
    )
    assert tools[2].description == "Cancel a running background subagent task."


def test_create_agent_task_tools_background_uses_filtered_task_description():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tools = create_agent_task_tools(
        provider=provider,
        profiles=[
            AgentProfile(name="general", description="General work."),
            AgentProfile(name="debugger", description="Debug failures."),
            AgentProfile(name="reviewer", description="Review changes."),
        ],
        base_config=RuntimeConfig(
            tool_permissions={"task": {"debugger": "deny", "*": "allow"}},
        ),
        allow_background=True,
    )

    assert [tool.id for tool in tools] == ["task", "task_status", "task_cancel"]
    assert tools[0].description == "\n".join(
        [
            "Delegate a task to an injected Runtime v2 task runner.",
            "",
            "Available agent types:",
            "- general: General work.",
            "- reviewer: Review changes.",
        ]
    )


def test_agent_task_tool_description_lists_default_general_profile():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    tool = create_agent_task_tool(provider=provider)

    assert "- general: This subagent should only be called manually by the user." in (
        tool.description
    )


@pytest.mark.asyncio
async def test_subagent_runner_selects_profile_and_builds_child_prompt():
    provider = ScriptedLLMProvider([{"content": "child complete"}])
    runner = create_subagent_task_runner(
        provider=provider,
        profiles=[
            AgentProfile(name="general"),
            AgentProfile(
                name="debugger",
                prompt="Focus on failing tests and stack traces.",
                metadata={"tier": "specialist"},
            ),
        ],
        base_config=RuntimeConfig(max_iterations=1),
    )

    result = await runner(
        TaskToolRequest(
            description="Analyze logs",
            prompt="Find the failing step.",
            subagent_type="debugger",
            task_id="task 1",
            command="inspect-ci",
            session_id="parent session",
        )
    )

    assert result.state == "completed"
    assert result.text == "child complete"
    assert result.metadata["parent_session_id"] == "parent session"
    assert result.metadata["task_id"] == "task 1"
    assert result.metadata["subagent_type"] == "debugger"
    assert result.metadata["agent_profile"] == "debugger"
    assert result.metadata["profile_name"] == "debugger"
    assert result.metadata["child_status"] == LoopStatus.COMPLETED
    assert " " not in result.metadata["child_session_id"]
    assert "parent_session" in result.metadata["child_session_id"]
    assert "task_1" in result.metadata["child_session_id"]

    assert len(provider.requests) == 1
    request = provider.requests[0]
    child_prompt = request.messages[0].parts[0].text
    assert child_prompt is not None
    assert '<agent_instructions name="debugger">' in child_prompt
    assert "Focus on failing tests and stack traces." in child_prompt
    assert '<task_prompt description="Analyze logs" command="inspect-ci">' in child_prompt
    assert "Find the failing step." in child_prompt
    assert request.metadata["agent_profile"] == "debugger"
    assert request.metadata["subagent_type"] == "debugger"
    assert request.session_id == result.metadata["child_session_id"]


@pytest.mark.asyncio
async def test_create_agent_task_tool_foreground_execution_still_selects_profile():
    provider = ScriptedLLMProvider([{"content": "debug complete"}])
    tool = create_agent_task_tool(
        provider=provider,
        profiles=[
            AgentProfile(name="general"),
            AgentProfile(
                name="debugger",
                description="Debug failures.",
                prompt="Use the debugger profile.",
            ),
        ],
        base_config=RuntimeConfig(max_iterations=1),
    )
    runtime = ToolRuntime(ToolRegistry([tool]))

    result = await runtime.execute(
        ToolCall(
            id="call-agent-task",
            tool_id="task",
            args={
                "description": "Analyze logs",
                "prompt": "Find the failing step.",
                "subagent_type": "debugger",
                "task_id": "task-agent-foreground",
            },
        ),
        context=ToolContext(session_id="parent-agent-task"),
    )

    assert result.status == "success"
    assert result.output["text"] == "debug complete"
    request = provider.requests[0]
    assert request.metadata["agent_profile"] == "debugger"
    assert request.metadata["parent_session_id"] == "parent-agent-task"
    assert "Use the debugger profile." in request.messages[0].parts[0].text


@pytest.mark.asyncio
async def test_profile_tools_are_applied_as_per_run_overrides():
    provider = ScriptedLLMProvider([{"content": "done"}])

    def tool_runtime_factory(profile: AgentProfile) -> ToolRuntime:
        assert profile.name == "restricted"
        return ToolRuntime(ToolRegistry([_tool("alpha"), _tool("beta")]))

    runner = create_subagent_task_runner(
        provider=provider,
        profiles=[
            AgentProfile(
                name="restricted",
                tools={"beta": False},
            )
        ],
        base_config=RuntimeConfig(
            max_iterations=1,
            enabled_tools=["alpha", "beta"],
        ),
        tool_runtime_factory=tool_runtime_factory,
    )

    result = await runner(
        TaskToolRequest(
            description="Use restricted tools",
            prompt="Answer with available tools.",
            subagent_type="restricted",
            task_id="task-tools",
            session_id="parent-tools",
        )
    )

    assert result.state == "completed"
    request = provider.requests[0]
    assert [tool.id for tool in request.tools] == ["alpha"]
    assert [schema.id for schema in request.provider_request.tools] == ["alpha"]
    assert request.metadata["enabled_tool_ids"] == ["alpha"]
    assert request.metadata["disabled_tool_ids"] == ["beta"]
    assert request.provider_request.metadata["tools"] == {
        "enabled": ["alpha"],
        "disabled": ["beta"],
    }


@pytest.mark.asyncio
async def test_subagent_child_tools_hide_recursive_task_and_todos_by_default():
    visible_tool_ids = await _visible_child_tool_ids(AgentProfile(name="guarded"))

    assert visible_tool_ids == ["alpha"]


@pytest.mark.asyncio
async def test_subagent_child_tools_keep_guarded_tools_with_direct_permission():
    visible_tool_ids = await _visible_child_tool_ids(
        AgentProfile(
            name="guarded",
            metadata={"permission": {"task": "allow", "todowrite": "ask"}},
        )
    )

    assert visible_tool_ids == ["alpha", "task", "todo_write", "todowrite"]


@pytest.mark.asyncio
async def test_subagent_child_tools_keep_guarded_tools_with_nested_permission():
    visible_tool_ids = await _visible_child_tool_ids(
        AgentProfile(
            name="guarded",
            metadata={
                "permission": {
                    "task": {"general": "allow"},
                    "todowrite": {"*": "ask"},
                }
            },
        )
    )

    assert visible_tool_ids == ["alpha", "task", "todo_write", "todowrite"]


@pytest.mark.asyncio
async def test_subagent_child_tools_do_not_treat_wildcard_permission_as_opt_in():
    visible_tool_ids = await _visible_child_tool_ids(
        AgentProfile(
            name="guarded",
            metadata={"permission": {"*": "allow"}},
        )
    )

    assert visible_tool_ids == ["alpha"]


@pytest.mark.asyncio
async def test_subagent_profile_tools_cannot_reenable_guarded_tools():
    visible_tool_ids = await _visible_child_tool_ids(
        AgentProfile(
            name="guarded",
            tools={"task": True, "todowrite": True},
        )
    )

    assert visible_tool_ids == ["alpha"]


@pytest.mark.asyncio
async def test_subagent_profile_tools_false_wins_over_guard_permission():
    visible_tool_ids = await _visible_child_tool_ids(
        AgentProfile(
            name="guarded",
            tools={"task": False},
            metadata={"permission": {"task": "allow", "todowrite": "allow"}},
        )
    )

    assert visible_tool_ids == ["alpha", "todo_write", "todowrite"]


def test_child_config_merges_profile_permission_over_base_without_mutation(
    tmp_path: Path,
):
    profile_permission = {
        "alpha": "deny",
        "gamma": {"action": "ask", "reason": "Review gamma."},
    }
    profile = AgentProfile(
        name="reviewer",
        metadata={"permission": profile_permission},
    )
    base_config = RuntimeConfig(
        workspace_root=tmp_path,
        tool_permissions={"alpha": "allow", "beta": "deny"},
        inject_background_task_results=False,
    )

    child_config = _child_config(
        profile=profile,
        base_config=base_config,
        workspace_root=None,
        metadata={"child": True},
    )

    assert child_config.tool_permissions == {
        "alpha": "deny",
        "beta": "deny",
        "gamma": {"action": "ask", "reason": "Review gamma."},
    }
    assert base_config.tool_permissions == {"alpha": "allow", "beta": "deny"}
    assert profile.metadata == {"permission": profile_permission}
    assert child_config.tool_permissions is not base_config.tool_permissions
    assert child_config.inject_background_task_results is False


@pytest.mark.asyncio
async def test_child_profile_permission_overlay_denies_base_allowed_tool(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "blocked.txt",
                                    "content": "blocked",
                                },
                                sort_keys=True,
                            ),
                        },
                    }
                ]
            },
            {"content": "done"},
        ]
    )
    profile = AgentProfile(
        name="reviewer",
        metadata={"permission": {"edit": "deny"}},
    )
    base_config = RuntimeConfig(
        workspace_root=tmp_path,
        max_iterations=2,
        tool_permissions={"write_file": "allow"},
    )
    runner = create_subagent_task_runner(
        provider=provider,
        profiles=[profile],
        base_config=base_config,
    )

    result = await runner(
        TaskToolRequest(
            description="Write",
            prompt="Write the file.",
            subagent_type="reviewer",
            task_id="task-permission",
            session_id="parent-permission",
        )
    )

    tool_message = provider.requests[1].messages[-1]
    tool_result = tool_message.parts[0].tool_result

    assert result.state == "completed"
    assert result.text == "done"
    assert tool_result is not None
    assert tool_result.status == "permission_denied"
    assert tool_result.error == "Permission denied by agent permission overlay: edit"
    assert (tmp_path / "blocked.txt").exists() is False
    assert provider.requests[0].metadata["agent_permission_overlay"] == {
        "edit": "deny"
    }
    assert base_config.tool_permissions == {"write_file": "allow"}
    assert profile.metadata == {"permission": {"edit": "deny"}}


@pytest.mark.asyncio
async def test_profile_active_skills_enter_child_context_without_base_pollution(
    tmp_path: Path,
):
    _write_skill(tmp_path, "review-pr", content="# Review\nInspect diffs.")
    provider = ScriptedLLMProvider([{"content": "review done"}])
    base_config = RuntimeConfig(
        skill_directories=[tmp_path],
        active_skills=["base-skill"],
        max_iterations=1,
        include_default_system_prompt=False,
        include_runtime_reminders=False,
    )
    runner = create_subagent_task_runner(
        provider=provider,
        profiles=[
            AgentProfile(name="reviewer", active_skills=["review-pr"]),
        ],
        base_config=base_config,
    )

    result = await runner(
        TaskToolRequest(
            description="Review",
            prompt="Review the change.",
            subagent_type="reviewer",
            task_id="task-skill",
            session_id="parent-skill",
        )
    )

    assert result.state == "completed"
    request = provider.requests[0]
    assert request.provider_request.messages[0].role == "system"
    assert '<skill_content name="review-pr">' in request.provider_request.messages[0].text
    assert "# Skill: review-pr" in request.provider_request.messages[0].text
    assert request.provider_request.messages[1].role == "user"
    assert request.metadata["active_skills"] == ["review-pr"]
    assert request.provider_request.metadata["active_skills"] == ["review-pr"]
    assert base_config.active_skills == ["base-skill"]
    assert [message.role for message in request.messages] == [MessageRole.USER]


@pytest.mark.asyncio
async def test_subagent_non_completed_and_runtime_errors_return_task_error_state():
    max_provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-again",
                        "type": "function",
                        "function": {"name": "again", "arguments": "{}"},
                    }
                ]
            }
        ]
    )
    max_runner = create_subagent_task_runner(
        provider=max_provider,
        profiles=[AgentProfile(name="looper", max_iterations=1)],
    )

    max_result = await max_runner(
        TaskToolRequest(
            description="Loop",
            prompt="Call a tool.",
            subagent_type="looper",
            task_id="task-max",
            session_id="parent-max",
        )
    )

    assert max_result.state == "error"
    assert max_result.metadata["child_status"] == LoopStatus.MAX_ITERATIONS
    assert "max_iterations" in max_result.text

    error_provider = ScriptedLLMProvider([{"content": "unused"}])
    error_runner = create_subagent_task_runner(
        provider=error_provider,
        profiles=[AgentProfile(name="bad-tools", tools={"missing": False})],
    )

    error_result = await error_runner(
        TaskToolRequest(
            description="Bad tools",
            prompt="This should not reach the provider.",
            subagent_type="bad-tools",
            task_id="task-error",
            session_id="parent-error",
        )
    )

    assert error_result.state == "error"
    assert error_result.metadata["child_status"] == LoopStatus.ERROR
    assert "Unknown tool: missing" in error_result.text
    assert error_provider.requests == []

    class RaisingProvider:
        def __init__(self) -> None:
            self.requests: list[RuntimeRequest] = []

        async def invoke(self, request: RuntimeRequest) -> dict[str, Any]:
            self.requests.append(request)
            raise RuntimeError("provider exploded")

    raising_provider = RaisingProvider()
    raising_runner = create_subagent_task_runner(
        provider=raising_provider,
        profiles=[AgentProfile(name="raiser")],
    )

    raising_result = await raising_runner(
        TaskToolRequest(
            description="Provider error",
            prompt="Trigger provider failure.",
            subagent_type="raiser",
            task_id="task-provider-error",
            session_id="parent-provider-error",
        )
    )

    assert raising_result.state == "error"
    assert raising_result.metadata["child_status"] == LoopStatus.ERROR
    assert raising_result.text == "provider exploded"
    assert len(raising_provider.requests) == 1


@pytest.mark.asyncio
async def test_create_task_tool_with_subagent_runner_returns_output_to_parent_loop():
    class ParentChildProvider:
        def __init__(self) -> None:
            self.requests: list[RuntimeRequest] = []

        async def invoke(self, request: RuntimeRequest) -> dict[str, Any]:
            self.requests.append(request)
            if request.metadata.get("agent_profile") == "debugger":
                assert request.metadata["parent_session_id"] == "parent-session"
                assert request.metadata["task_id"] == "task-loop"
                assert request.session_id == "subagent-parent-session-task-loop"
                return {"content": "child analysis"}

            if request.iteration == 1:
                return {
                    "tool_calls": [
                        {
                            "id": "call-task-loop",
                            "type": "function",
                            "function": {
                                "name": "task",
                                "arguments": json.dumps(
                                    {
                                        "description": "Analyze logs",
                                        "prompt": "Find the failing step.",
                                        "subagent_type": "debugger",
                                        "task_id": "task-loop",
                                    },
                                    sort_keys=True,
                                ),
                            },
                        }
                    ]
                }

            assert request.iteration == 2
            assert request.messages[-1].role is MessageRole.TOOL
            result_part = request.messages[-1].parts[0]
            assert result_part.type is MessagePartType.TOOL_RESULT
            assert result_part.tool_result is not None
            assert result_part.tool_result.call_id == "call-task-loop"
            assert "<task_result>\nchild analysis\n</task_result>" in (
                result_part.tool_result.content
            )
            assert result_part.tool_result.metadata["task_result_metadata"][
                "child_session_id"
            ] == "subagent-parent-session-task-loop"
            return {"content": "parent final"}

    provider = ParentChildProvider()
    task_runner = create_subagent_task_runner(
        provider=provider,
        profiles=[
            AgentProfile(name="general"),
            AgentProfile(name="debugger", prompt="Debug the task."),
        ],
        base_config=RuntimeConfig(max_iterations=1),
    )
    runner = RuntimeLoopRunner(
        store=_store(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry([create_task_tool(task_runner)])),
        max_iterations=3,
    )

    result = await runner.run(session_id="parent-session", user_text="Delegate.")

    assert result.status == LoopStatus.COMPLETED
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == "parent final"
    assert len(provider.requests) == 3


def test_agent_profiles_import_boundary():
    code = """
import importlib
import json
import sys

importlib.import_module("efp_runtime.agents")
legacy_modules = [
    "src.sessions",
    "src.agents.core",
    "src.runtime",
    "src.skills",
]
print(json.dumps({
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
    assert payload == {"legacy_loaded": []}


def _tool(tool_id: str) -> ToolDef:
    async def execute(args, context):
        return {"tool": tool_id, "session_id": context.session_id}

    return ToolDef(
        id=tool_id,
        description=f"{tool_id} tool",
        input_schema={"type": "object", "properties": {}},
        execute=execute,
    )


async def _visible_child_tool_ids(profile: AgentProfile) -> list[str]:
    provider = ScriptedLLMProvider([{"content": "done"}])

    def tool_runtime_factory(selected_profile: AgentProfile) -> ToolRuntime:
        assert selected_profile.name == profile.name
        return ToolRuntime(
            ToolRegistry(
                [
                    _tool("alpha"),
                    _tool("task"),
                    _tool("todo_write"),
                    _tool("todowrite"),
                ]
            )
        )

    runner = create_subagent_task_runner(
        provider=provider,
        profiles=[profile],
        base_config=RuntimeConfig(max_iterations=1),
        tool_runtime_factory=tool_runtime_factory,
    )
    result = await runner(
        TaskToolRequest(
            description="Inspect child tools",
            prompt="Return without using tools.",
            subagent_type=profile.name,
            task_id=f"task-{profile.name}",
            session_id="parent-guarded-tools",
        )
    )

    assert result.state == "completed", result.text
    assert len(provider.requests) == 1
    return [tool.id for tool in provider.requests[0].tools]


def _write_skill(
    tmp_path: Path,
    name: str,
    *,
    description: str = "Loads skill context",
    content: str = "# Skill\nUse this context.",
) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{content}\n",
        encoding="utf-8",
    )
    return skill_dir


def _store():
    from efp_runtime.session.store import InMemorySessionStore

    return InMemorySessionStore()
