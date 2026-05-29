from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.permissions import PermissionDecision, PermissionMetadata
from efp_runtime.session.models import MessagePart, MessagePartType, MessageRole
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext, ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


class AllowEvaluator:
    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: ToolContext | None = None,
    ) -> PermissionDecision:
        return PermissionDecision.allow()


def _python_command(script: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


@pytest.mark.asyncio
async def test_tool_runtime_injects_tool_call_context_fields_and_metadata():
    captured: dict[str, ToolContext] = {}

    async def execute(args, context):
        captured["context"] = context
        return "ok"

    runtime = ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id="capture",
                    description="Capture context",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                )
            ]
        )
    )

    result = await runtime.execute(
        ToolCall(id="call-context", tool_id="capture", args={}),
        context=ToolContext(
            session_id="session-context",
            request_id="request-context",
            run_id="run-context",
            iteration=7,
        ),
    )

    context = captured["context"]
    assert context.session_id == "session-context"
    assert context.request_id == "request-context"
    assert context.tool_call_id == "call-context"
    assert context.tool_name == "capture"
    assert context.run_id == "run-context"
    assert context.iteration == 7
    assert context.metadata["tool_call_id"] == "call-context"
    assert context.metadata["tool_name"] == "capture"
    assert context.metadata["run_id"] == "run-context"
    assert context.metadata["iteration"] == 7
    assert result.events[-1].payload["tool_call_id"] == "call-context"
    assert result.events[-1].payload["tool_name"] == "capture"
    assert result.events[-1].payload["run_id"] == "run-context"


@pytest.mark.asyncio
async def test_loop_runner_passes_run_tool_call_and_iteration_context():
    captured: list[ToolContext] = []

    async def execute(args, context):
        captured.append(context)
        return f"echo:{context.run_id}:{context.iteration}:{context.tool_call_id}"

    store = InMemorySessionStore()
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-loop",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ]
            },
            {"content": "Done."},
        ]
    )
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="echo",
                        description="Echo context",
                        input_schema={"type": "object", "properties": {}},
                        execute=execute,
                    )
                ]
            )
        ),
        max_iterations=3,
    )

    result = await runner.run(
        session_id="session-loop",
        user_text="Use echo.",
        metadata={"run_id": "run-loop"},
    )

    assert result.status == LoopStatus.COMPLETED
    context = captured[0]
    assert context.session_id == "session-loop"
    assert context.request_id == "run-loop"
    assert context.run_id == "run-loop"
    assert context.tool_call_id == "call-loop"
    assert context.tool_name == "echo"
    assert context.iteration == 1
    assert context.metadata["run_id"] == "run-loop"
    assert context.metadata["tool_call_id"] == "call-loop"
    assert context.metadata["iteration"] == 1

    completed_events = [
        event for event in result.runtime_events if event.type == "tool.completed"
    ]
    assert completed_events[-1].payload["run_id"] == "run-loop"
    assert completed_events[-1].payload["tool_call_id"] == "call-loop"
    assert completed_events[-1].payload["tool_name"] == "echo"


@pytest.mark.asyncio
async def test_resume_pending_tool_call_has_context_and_does_not_append_user_message():
    captured: list[ToolContext] = []

    async def execute(args, context):
        captured.append(context)
        return "resumed"

    store = InMemorySessionStore()
    store.create_session(session_id="session-resume")
    store.append_message(
        "session-resume",
        role=MessageRole.USER,
        parts=[MessagePart.text_part("Use echo.")],
        status="complete",
    )
    store.append_message(
        "session-resume",
        role=MessageRole.ASSISTANT,
        parts=[
            MessagePart.tool_call_part(
                ToolCall(id="call-resume", tool_id="echo", args={})
            )
        ],
        status="complete",
    )
    runner = RuntimeLoopRunner(
        store=store,
        provider=ScriptedLLMProvider([{"content": "Resumed."}]),
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="echo",
                        description="Echo context",
                        input_schema={"type": "object", "properties": {}},
                        execute=execute,
                    )
                ]
            )
        ),
        max_iterations=2,
    )

    result = await runner.run(
        user_text="",
        session_id="session-resume",
        append_user_message=False,
        metadata={"run_id": "run-resume", "resume": True},
    )

    assert result.status == LoopStatus.COMPLETED
    context = captured[0]
    assert context.run_id == "run-resume"
    assert context.request_id == "run-resume"
    assert context.tool_call_id == "call-resume"
    assert context.tool_name == "echo"
    assert context.iteration is None
    assert context.metadata["run_id"] == "run-resume"
    assert context.metadata["tool_call_id"] == "call-resume"
    assert context.metadata["iteration"] == "resume"
    assert context.metadata["resume"] is True

    history = store.read_history("session-resume")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert sum(1 for message in history if message.role is MessageRole.USER) == 1
    assert history[2].parts[0].type is MessagePartType.TOOL_RESULT
    assert history[2].parts[0].tool_result.call_id == "call-resume"


@pytest.mark.asyncio
async def test_bash_saves_full_output_when_not_truncated(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-shell-full",
            tool_id="bash",
            args={"command": _python_command("print('visible-output')")},
        ),
        context=ToolContext(session_id="session-shell", run_id="run-shell"),
    )

    assert result.status == "success"
    assert result.truncated is False
    assert result.metadata["truncated"] is False
    assert result.metadata["tool_call_id"] == "call-shell-full"
    assert result.metadata["run_id"] == "run-shell"
    assert result.metadata["visible_output_chars"] == len(result.content)

    output_path = result.metadata["output_path"]
    saved_path = (tmp_path / output_path).resolve()
    saved_path.relative_to(tmp_path.resolve())
    saved_content = saved_path.read_text(encoding="utf-8")
    assert saved_content == result.content
    assert result.metadata["full_output_chars"] == len(saved_content)
    assert Path(output_path).stem == "call-shell-full"


@pytest.mark.asyncio
async def test_bash_truncates_visible_content_but_saves_complete_output(
    tmp_path: Path,
):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-shell-truncated",
            tool_id="bash",
            args={
                "command": _python_command(
                    "import sys\n"
                    "for i in range(40): print('stdout-%03d' % i)\n"
                    "print('stderr-complete', file=sys.stderr)\n"
                ),
                "max_output_chars": 180,
                "max_output_lines": 5,
            },
        ),
        context=ToolContext(session_id="session-shell", run_id="run-shell"),
    )

    assert result.status == "success"
    assert result.truncated is True
    assert result.metadata["truncated"] is True
    assert result.content.startswith("...output truncated...")
    assert "stdout-000" not in result.content
    assert result.metadata["visible_output_chars"] == len(result.content)

    output_path = result.metadata["output_path"]
    saved_path = (tmp_path / output_path).resolve()
    saved_path.relative_to(tmp_path.resolve())
    saved_content = saved_path.read_text(encoding="utf-8")
    assert "stdout-000" in saved_content
    assert "stdout-039" in saved_content
    assert "stderr-complete" in saved_content
    assert result.metadata["full_output_chars"] == len(saved_content)
    assert Path(output_path).stem == "call-shell-truncated"


def test_tool_context_output_parity_sources_stay_inside_runtime_v2_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            ROOT / "src/efp_runtime/tools/definition.py",
            ROOT / "src/efp_runtime/tools/runtime.py",
            ROOT / "src/efp_runtime/tools/builtin/shell.py",
            ROOT / "src/efp_runtime/tools/builtin/output.py",
            ROOT / "src/efp_runtime/loop/runner.py",
        ]
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.sessions",
        "import src.sessions",
        "from src.agents.core",
        "import src.agents.core",
        "from src.runtime",
        "import src.runtime",
        "from src.skills",
        "import src.skills",
    ]
    for token in forbidden_tokens:
        assert token not in combined
