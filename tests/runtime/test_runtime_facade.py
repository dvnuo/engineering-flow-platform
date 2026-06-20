from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime import MessageRole
from efp_runtime.commands import CommandDefinition, CommandRegistry
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_agent_runtime_defaults_to_builtin_tools_for_workspace(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "Facade done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=2,
            metadata={"suite": "facade"},
        ),
    )

    result = await runtime.run(
        "Use the facade.",
        session_id="session-facade",
        metadata={"request_id": "run-1"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert result.iterations == 1
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == "Facade done."
    assert runtime.tool_runtime.registry.ids() == [
        "apply_patch",
        "bash",
        "edit",
        "glob",
        "grep",
        "invalid",
        "read",
        "skill",
        "task",
        "todowrite",
        "webfetch",
        "write",
    ]

    request = provider.requests[0]
    assert request.provider_request.messages[0].role == "user"
    assert request.provider_request.messages[-1].role == "user"
    assert request.provider_request.messages[-1].text == "Use the facade."
    assert [schema.id for schema in request.provider_request.tools] == [
        "apply_patch",
        "bash",
        "glob",
        "grep",
        "invalid",
        "read",
        "skill",
        "task",
        "todowrite",
        "webfetch",
    ]
    assert request.metadata["suite"] == "facade"
    assert request.metadata["request_id"] == "run-1"
    assert request.metadata["system_prompt_context_count"] == 0
    assert request.metadata["environment_context_count"] == 0
    assert request.metadata["instruction_context_count"] == 0
    assert request.metadata["skill_context_count"] == 0
    assert request.metadata["loop"]["iteration"] == 1
    assert request.provider_request.metadata["loop"]["max_iterations"] == 2


@pytest.mark.asyncio
async def test_agent_runtime_session_management_facade_with_default_store(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [
            {"content": "Original answer."},
            {"content": "Fork resumed."},
            {"content": "Fork continued."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=2),
    )

    created = runtime.create_session(
        session_id="session-b",
        title="Runtime source",
        metadata={"suite": "facade"},
    )
    assert runtime.get_session(created.session_id).title == "Runtime source"

    result = await runtime.run("Start source.", session_id=created.session_id)
    assert result.status == LoopStatus.COMPLETED
    source_messages = runtime.session_messages(created.session_id)
    assert [message.role for message in source_messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert source_messages[1].parts[0].text == "Original answer."

    fork = runtime.fork_session(
        created.session_id,
        message_id=source_messages[0].message_id,
        new_session_id="session-a",
    )
    assert [session.session_id for session in runtime.list_sessions()] == [
        "session-a",
        "session-b",
    ]
    assert fork.metadata == {
        "suite": "facade",
        "parent_session_id": "session-b",
        "forked_from_message_id": source_messages[0].message_id,
    }
    assert [session.session_id for session in runtime.session_children("session-b")] == [
        "session-a"
    ]

    resume_result = await runtime.resume(fork.session_id)
    assert resume_result.status == LoopStatus.COMPLETED
    assert runtime.session_messages(fork.session_id)[1].parts[0].text == "Fork resumed."

    run_result = await runtime.run("Continue fork.", session_id=fork.session_id)
    assert run_result.status == LoopStatus.COMPLETED
    fork_messages = runtime.session_messages(fork.session_id)
    assert [message.role for message in fork_messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert fork_messages[-1].parts[0].text == "Fork continued."
    assert [message.parts[0].text for message in runtime.session_messages("session-b")] == [
        "Start source.",
        "Original answer.",
    ]

    assert runtime.delete_session(fork.session_id) is True
    assert runtime.delete_session(fork.session_id) is False
    assert runtime.session_children("session-b") == []
    assert [session.session_id for session in runtime.list_sessions()] == ["session-b"]


@pytest.mark.asyncio
async def test_agent_runtime_switch_model_sets_session_default_for_run_and_resume():
    command_registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="build",
                content="Build.",
                source="config",
                model="command/model",
            )
        ]
    )
    provider = ScriptedLLMProvider(
        [
            {"content": "Session model."},
            {"content": "Caller model."},
            {"content": "Command model."},
            {"content": "Resume model."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=2,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=command_registry,
    )
    runtime.create_session(session_id="session-model")

    switched = runtime.switch_model("session-model", " session/model ")

    assert switched.metadata["model"] == "session/model"
    assert switched.metadata["requested_model"] == "session/model"
    event = runtime.event_bus.history("session-model")[0]
    assert event.type == "session.model_switched"
    assert event.message == "Session model switched."
    assert event.payload == {"model": "session/model"}

    await runtime.run("Use session model.", session_id="session-model")
    await runtime.run(
        "Use caller model.",
        session_id="session-model",
        metadata={"requested_model": "caller/model"},
    )
    await runtime.run("/build", session_id="session-model")
    await runtime.resume("session-model")

    assert provider.requests[0].metadata["requested_model"] == "session/model"
    assert provider.requests[0].provider_request.metadata["requested_model"] == (
        "session/model"
    )
    assert provider.requests[1].metadata["requested_model"] == "caller/model"
    assert provider.requests[2].metadata["requested_model"] == "command/model"
    assert provider.requests[2].metadata["command_model"] == "command/model"
    assert provider.requests[3].metadata["requested_model"] == "session/model"


@pytest.mark.asyncio
async def test_agent_runtime_switch_model_mapping_sets_session_model_without_string():
    provider = ScriptedLLMProvider([{"content": "Mapped model."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=2,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )
    runtime.create_session(session_id="session-model-mapping")
    runtime.switch_model("session-model-mapping", "old/model")
    model = {"provider": "custom", "model": "mapped/model", "options": {"temp": 0.2}}

    switched = runtime.switch_model("session-model-mapping", model)
    model["options"]["temp"] = 1.0

    assert switched.metadata["model"] == {
        "provider": "custom",
        "model": "mapped/model",
        "options": {"temp": 0.2},
    }
    assert "requested_model" not in switched.metadata
    assert runtime.event_bus.history("session-model-mapping")[-1].payload == {
        "model": {
            "provider": "custom",
            "model": "mapped/model",
            "options": {"temp": 0.2},
        }
    }

    await runtime.run("Use mapping.", session_id="session-model-mapping")

    assert provider.requests[0].metadata["session_model"] == {
        "provider": "custom",
        "model": "mapped/model",
        "options": {"temp": 0.2},
    }
    assert "requested_model" not in provider.requests[0].metadata


def test_runtime_facade_imports_standalone_with_pythonpath_src():
    code = """
import json
import sys

import efp_runtime.runtime

print(json.dumps({"legacy_core_loaded": "src.agents.core" in sys.modules}))
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
    assert payload == {"legacy_core_loaded": False}


def test_runtime_facade_source_stays_inside_runtime_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime/runtime").rglob("*.py"))
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.agents.core",
        "import src.agents.core",
        "Agent.process(",
        "SkillSession(",
        "SkillsExecutor(",
        "from src.agents.tool_result_policy",
        "import src.agents.tool_result_policy",
        "src.bash_tools",
        "src.github",
        "src.jira",
        "src.confluence",
    ]
    for token in forbidden_tokens:
        assert token not in combined
