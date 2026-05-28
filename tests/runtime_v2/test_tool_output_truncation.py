from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.models import MessagePartType
from efp_runtime.tools.definition import OutputPolicy, ToolContext, ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.tools.truncation import ToolOutputTruncator, TruncationLimits
from efp_runtime.types import ToolCall, ToolResult


ROOT = Path(__file__).resolve().parents[2]


def test_small_output_is_not_truncated(tmp_path: Path):
    truncator = ToolOutputTruncator(tmp_path / "tool-output")
    text = "alpha\nbeta\n"

    result = truncator.truncate(text)

    assert result.content == text
    assert result.truncated is False
    assert result.metadata["truncated"] is False
    assert result.metadata["original_chars"] == len(text)
    assert result.metadata["original_bytes"] == len(text.encode("utf-8"))
    assert result.metadata["original_lines"] == len(text.splitlines())


def test_max_lines_truncates_and_archives_full_output(tmp_path: Path):
    text = "\n".join(f"line-{index}" for index in range(6))
    truncator = ToolOutputTruncator(
        tmp_path / "tool-output",
        limits=TruncationLimits(max_lines=2, max_bytes=1024),
    )

    result = truncator.truncate(text)

    assert result.truncated is True
    assert result.metadata["truncated"] is True
    assert result.metadata["truncated_by"] == ["lines"]
    assert result.metadata["removed"]["lines"] == 4
    assert "line-0" in result.content
    assert "line-1" in result.content
    assert "line-5" not in result.content
    assert "lines/" in result.content
    assert "read_file" in result.content
    assert "grep" in result.content

    output_path = Path(result.metadata["output_path"])
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == text


def test_max_bytes_truncates_using_utf8_bytes(tmp_path: Path):
    text = "éééé"
    truncator = ToolOutputTruncator(
        tmp_path / "tool-output",
        limits=TruncationLimits(max_lines=100, max_bytes=5),
    )

    result = truncator.truncate(text)

    assert result.truncated is True
    assert result.metadata["original_chars"] == 4
    assert result.metadata["original_bytes"] == 8
    assert result.metadata["truncated_by"] == ["bytes"]
    assert "éé" in result.content
    assert "ééé" not in result.content
    assert "bytes truncated" in result.content


def test_tail_direction_preserves_tail_preview(tmp_path: Path):
    text = "\n".join(f"line-{index}" for index in range(5))
    truncator = ToolOutputTruncator(
        tmp_path / "tool-output",
        limits=TruncationLimits(max_lines=2, max_bytes=1024, direction="tail"),
    )

    result = truncator.truncate(text)

    assert result.truncated is True
    assert "line-0" not in result.content
    assert "line-3" in result.content
    assert "line-4" in result.content


@pytest.mark.asyncio
async def test_tool_result_declared_truncated_is_not_truncated_again(tmp_path: Path):
    content = "\n".join(f"line-{index}" for index in range(10))

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(
            call_id=context.tool_call_id or "call-inner",
            tool_name="long",
            content=content,
            metadata={"truncated": True},
            truncated=True,
        )

    runtime = ToolRuntime(
        ToolRegistry([_tool("long", execute=execute)]),
        default_output_policy=OutputPolicy(max_lines=2, max_bytes=1024),
        output_truncator=ToolOutputTruncator(tmp_path / "tool-output"),
    )

    result = await runtime.execute(ToolCall(id="call-long", tool_id="long", args={}))

    assert result.content == content
    assert result.truncated is True
    assert result.metadata["truncated"] is True
    assert "output_path" not in result.metadata
    assert not (tmp_path / "tool-output").exists()


@pytest.mark.asyncio
async def test_agent_runtime_archives_under_workspace_and_preserves_explicit_runtime(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-long", "long")]},
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            tool_output_max_lines=2,
            tool_output_max_bytes=1024,
        ),
        tool_registry=ToolRegistry([_tool("long", output=_long_output())]),
    )

    result = await runtime.run("run long", session_id="session-tool-output")

    assert result.status == LoopStatus.COMPLETED
    tool_result = _first_tool_result(runtime.store.read_history("session-tool-output"))
    assert tool_result.truncated is True
    output_path = Path(tool_result.metadata["output_path"])
    output_path.relative_to(tmp_path.resolve())
    assert output_path.parent == tmp_path / ".efp_runtime" / "tool-output"
    assert output_path.read_text(encoding="utf-8") == _long_output()

    explicit_runtime = ToolRuntime(ToolRegistry([_tool("long")]))
    explicit = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "done"}]),
        config=RuntimeConfig(workspace_root=tmp_path, tool_output_max_lines=1),
        tool_runtime=explicit_runtime,
    )

    assert explicit.tool_runtime is explicit_runtime
    assert explicit.tool_runtime.output_truncator is None


def test_tool_output_truncation_sources_stay_inside_runtime_v2_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            ROOT / "src/efp_runtime/tools/truncation.py",
            ROOT / "src/efp_runtime/tools/runtime.py",
            ROOT / "src/efp_runtime/runtime/agent.py",
            ROOT / "src/efp_runtime/runtime/config.py",
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


def _tool(
    tool_id: str,
    *,
    output: str = "ok",
    execute=None,
) -> ToolDef:
    async def default_execute(args: dict[str, Any], context: ToolContext):
        return output

    return ToolDef(
        id=tool_id,
        description=f"{tool_id} tool",
        input_schema={"type": "object", "properties": {}},
        execute=execute or default_execute,
    )


def _tool_call(call_id: str, tool_name: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps({}, sort_keys=True),
        },
    }


def _long_output() -> str:
    return "\n".join(f"line-{index}" for index in range(8))


def _first_tool_result(history) -> ToolResult:
    for message in history:
        for part in message.parts:
            if part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None:
                return part.tool_result
    raise AssertionError("tool result not found")
