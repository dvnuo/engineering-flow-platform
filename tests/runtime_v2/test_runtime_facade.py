from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
            max_iterations=1,
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
        "edit",
        "fetch",
        "glob",
        "grep",
        "invalid",
        "list_dir",
        "read_file",
        "shell_exec",
        "shell_kill",
        "shell_status",
        "todo_write",
        "write_file",
    ]

    request = provider.requests[0]
    assert request.provider_request.messages[0].role == "system"
    assert "EFP Runtime v2" in request.provider_request.messages[0].text
    assert request.provider_request.messages[-1].role == "user"
    assert request.provider_request.messages[-1].text == "Use the facade."
    assert [schema.id for schema in request.provider_request.tools] == [
        "apply_patch",
        "edit",
        "fetch",
        "glob",
        "grep",
        "invalid",
        "list_dir",
        "read_file",
        "shell_exec",
        "shell_kill",
        "shell_status",
        "todo_write",
        "write_file",
    ]
    assert request.metadata["suite"] == "facade"
    assert request.metadata["request_id"] == "run-1"
    assert request.metadata["system_prompt_context_count"] == 2
    assert request.metadata["instruction_context_count"] == 0
    assert request.metadata["skill_context_count"] == 0
    assert request.metadata["loop"]["iteration"] == 1
    assert request.provider_request.metadata["loop"]["max_iterations"] == 1


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


def test_runtime_facade_source_stays_inside_runtime_v2_boundary():
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
