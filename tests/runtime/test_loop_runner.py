from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.session.models import Message, MessagePartType, MessageRole, Session
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_text_only_provider_response_creates_final_assistant_text():
    store = InMemorySessionStore()
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(session_id="session-text", user_text="Say done.")

    assert result.status == LoopStatus.COMPLETED
    assert result.iterations == 1
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].type is MessagePartType.TEXT
    assert result.final_assistant_message.parts[0].text == "Done."

    history = store.read_history(result.session_id)
    assert [message.role for message in history] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert history[0].parts[0].text == "Say done."
    assert history[1].status == "complete"

    request = provider.requests[0]
    assert request.provider_request.messages[0].role == "user"
    assert request.provider_request.messages[0].text == "Say done."
    assert request.prepared_request.request is request.provider_request
    assert request.metadata["loop"]["iteration"] == 1
    assert request.metadata["loop"]["max_iterations"] == 4


@pytest.mark.asyncio
async def test_tool_call_result_then_second_provider_response_creates_final_text():
    async def execute(args, context):
        return f"echo:{args['text']}:{context.session_id}"

    store = InMemorySessionStore()
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_echo",
                        "type": "function",
                        "function": {
                            "name": "echo",
                            "arguments": '{"text": "hello"}',
                        },
                    }
                ]
            },
            {"content": "The tool returned echo:hello:session-tools."},
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
                        description="Echo text",
                        input_schema={
                            "type": "object",
                            "required": ["text"],
                            "properties": {"text": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        execute=execute,
                    )
                ]
            )
        ),
    )

    result = await runner.run(session_id="session-tools", user_text="Use echo.")

    assert result.status == LoopStatus.COMPLETED
    assert result.iterations == 2
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == (
        "The tool returned echo:hello:session-tools."
    )

    history = store.read_history(result.session_id)
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert history[1].parts[0].type is MessagePartType.TOOL_CALL
    assert history[1].parts[0].tool_call.call_id == "call_echo"
    assert history[2].parts[0].type is MessagePartType.TOOL_RESULT
    assert history[2].parts[0].tool_result.call_id == "call_echo"
    assert history[2].parts[0].tool_result.content == "echo:hello:session-tools"

    assert len(provider.requests) == 2
    assert [message.role for message in provider.requests[1].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    assert [tool.id for tool in provider.requests[0].tools] == ["echo"]
    assert [tool.id for tool in provider.requests[1].tools] == ["echo"]
    assert [schema.id for schema in provider.requests[0].provider_request.tools] == ["echo"]
    assert provider.requests[0].provider_request.tools[0].json_schema["required"] == ["text"]


@pytest.mark.asyncio
async def test_max_context_parts_compacts_provider_request_metadata():
    store = InMemorySessionStore()
    provider = ScriptedLLMProvider([{"content": "Compacted answer."}])
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        max_context_parts=2,
    )
    session = Session(
        session_id="session-compact",
        messages=[
            Message.from_text("user", "old request", session_id="session-compact"),
            Message.from_text("assistant", "old answer", session_id="session-compact"),
        ],
    )

    result = await runner.run(
        session=session,
        user_text="latest request",
        metadata={"caller": "test"},
    )

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert request.prepared_request.compaction_applied is True
    assert request.prepared_request.compaction_metadata["max_parts"] == 2
    assert request.prepared_request.compaction_metadata["max_chars"] is None
    assert request.prepared_request.compaction_metadata["reserve_chars"] == 0
    assert request.prepared_request.compaction_metadata["compacted_part_count"] == 2
    assert request.prepared_request.compaction_metadata["compacted_message_count"] == 2
    assert request.prepared_request.compaction_metadata["compacted_tool_pair_count"] == 0
    assert request.prepared_request.compaction_metadata["compacted_chars"] > 0
    assert request.prepared_request.compaction_metadata["kept_chars"] > 0
    assert request.provider_request.metadata["caller"] == "test"
    assert request.provider_request.metadata["compaction"]["max_parts"] == 2
    assert request.provider_request.metadata["compaction"]["compacted_part_count"] == 2
    assert [message.role for message in request.provider_request.messages] == ["system", "user"]
    assert request.provider_request.messages[0].context[0].type == "compaction_summary"
    assert request.provider_request.messages[1].text == "latest request"


@pytest.mark.asyncio
async def test_max_iterations_stops_with_explicit_status_after_tool_results():
    async def execute(args, context):
        return "ok"

    store = InMemorySessionStore()
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_again",
                        "type": "function",
                        "function": {"name": "again", "arguments": "{}"},
                    }
                ]
            }
        ]
    )
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="again",
                        description="Return ok",
                        input_schema={"type": "object", "properties": {}},
                        execute=execute,
                    )
                ]
            )
        ),
        max_iterations=1,
    )

    result = await runner.run(session_id="session-max", user_text="Loop once.")

    assert result.status == LoopStatus.MAX_ITERATIONS
    assert result.iterations == 1
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].type is MessagePartType.TOOL_CALL
    assert any(event.type == "loop.max_iterations" for event in result.runtime_events)

    history = store.read_history(result.session_id)
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    assert history[2].parts[0].type is MessagePartType.TOOL_RESULT


def test_loop_package_imports_standalone_with_pythonpath_src():
    code = """
import json
import sys

import efp_runtime.loop.provider
import efp_runtime.loop.runner

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


def test_loop_package_source_boundaries():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime/loop").rglob("*.py"))
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
