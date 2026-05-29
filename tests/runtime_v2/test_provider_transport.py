from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.llm.provider import (
    GitHubCopilotProvider,
    OpenAICompatibleProvider,
    RecordingTransport,
)
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner
from efp_runtime.session.models import MessagePartType, MessageRole
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_non_stream_chat_provider_projects_payload_and_returns_text():
    transport = RecordingTransport(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Projected chat answer.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 11},
            }
        ]
    )
    provider = OpenAICompatibleProvider(
        model="gpt-test",
        transport=transport,
        instructions="Be direct.",
        metadata={"trace_id": "trace-1"},
    )
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry([_lookup_tool()])),
    )

    result = await runner.run(
        session_id="session-chat",
        user_text="Answer from chat.",
        metadata={"request_id": "request-1"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == "Projected chat answer."
    assert result.final_assistant_message.usage["total_tokens"] == 11

    payload = transport.payloads[0]
    assert payload["model"] == "gpt-test"
    assert payload["stream"] is False
    assert payload["messages"][0] == {"role": "system", "content": "Be direct."}
    assert payload["messages"][1] == {"role": "user", "content": "Answer from chat."}
    assert payload["tools"][0]["function"]["name"] == "lookup"
    assert payload["metadata"]["request_id"] == "request-1"
    assert payload["metadata"]["trace_id"] == "trace-1"
    assert payload["metadata"]["efp_projection"]["endpoint"] == "chat_completions"


@pytest.mark.asyncio
async def test_responses_endpoint_projects_input_tools_and_metadata():
    transport = RecordingTransport(
        [
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Projected responses answer."}
                        ],
                    }
                ]
            }
        ]
    )
    provider = OpenAICompatibleProvider(
        model="gpt-test",
        transport=transport,
        endpoint="responses",
        instructions="Use typed responses input.",
        metadata={"trace_id": "trace-responses"},
    )
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry([_lookup_tool()])),
    )

    result = await runner.run(
        session_id="session-responses",
        user_text="Answer from responses.",
        metadata={"request_id": "request-responses"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == "Projected responses answer."

    payload = transport.payloads[0]
    assert payload["model"] == "gpt-test"
    assert payload["instructions"] == "Use typed responses input."
    assert payload["stream"] is False
    assert "messages" not in payload
    assert payload["input"][0]["role"] == "user"
    input_item = payload["input"][0]["content"][0]
    assert input_item["type"] == "input_text"
    assert input_item["text"] == "Answer from responses."
    assert input_item["metadata"]["part_type"] == "text"
    assert payload["tools"][0]["name"] == "lookup"
    assert payload["metadata"]["request_id"] == "request-responses"
    assert payload["metadata"]["trace_id"] == "trace-responses"
    assert payload["metadata"]["efp_projection"]["endpoint"] == "responses"


@pytest.mark.asyncio
async def test_chat_provider_uses_requested_model_payload_hint_without_switching_provider():
    transport = RecordingTransport([_chat_response("Requested chat model.")])
    provider = OpenAICompatibleProvider(model="gpt-test", transport=transport)
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(
        session_id="session-chat-model-hint",
        user_text="Answer with requested model.",
        metadata={"requested_model": "gpt-override"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert provider.model == "gpt-test"
    assert transport.payloads[0]["model"] == "gpt-override"
    assert transport.payloads[0]["metadata"]["requested_model"] == "gpt-override"


@pytest.mark.asyncio
async def test_responses_provider_uses_requested_model_payload_hint_without_switching_provider():
    transport = RecordingTransport([_responses_response("Requested responses model.")])
    provider = OpenAICompatibleProvider(
        model="gpt-test",
        transport=transport,
        endpoint="responses",
    )
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(
        session_id="session-responses-model-hint",
        user_text="Answer with requested model.",
        metadata={"requested_model": "gpt-override"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert provider.model == "gpt-test"
    assert transport.payloads[0]["model"] == "gpt-override"
    assert transport.payloads[0]["metadata"]["requested_model"] == "gpt-override"


@pytest.mark.asyncio
async def test_github_copilot_provider_defaults_metadata_and_model_payload():
    transport = RecordingTransport([_chat_response("Copilot answer.")])
    provider = GitHubCopilotProvider(
        transport=transport,
        metadata={"trace_id": "trace-copilot"},
    )
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(
        session_id="session-copilot-provider",
        user_text="Answer with Copilot default.",
    )

    assert result.status == LoopStatus.COMPLETED
    assert provider.model == "gpt-5-mini"
    payload = transport.payloads[0]
    assert payload["model"] == "gpt-5-mini"
    assert payload["metadata"]["provider"] == "github-copilot"
    assert payload["metadata"]["provider_id"] == "github-copilot"
    assert payload["metadata"]["trace_id"] == "trace-copilot"


@pytest.mark.asyncio
async def test_github_copilot_provider_requested_model_only_changes_payload_model():
    transport = RecordingTransport([_chat_response("Copilot override.")])
    provider = GitHubCopilotProvider(transport=transport)
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(
        session_id="session-copilot-model-hint",
        user_text="Answer with requested model.",
        metadata={"requested_model": "gpt-5"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert provider.model == "gpt-5-mini"
    assert transport.payloads[0]["model"] == "gpt-5"
    assert transport.payloads[0]["metadata"]["provider_id"] == "github-copilot"


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_model", ["", "   ", 123, None, ["gpt-override"]])
async def test_provider_requested_model_payload_hint_falls_back_for_blank_or_non_string(
    requested_model,
):
    transport = RecordingTransport([_chat_response("Base model.")])
    provider = OpenAICompatibleProvider(model="gpt-test", transport=transport)
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(
        session_id="session-model-hint-fallback-{0}".format(type(requested_model).__name__),
        user_text="Answer with base model.",
        metadata={"requested_model": requested_model},
    )

    assert result.status == LoopStatus.COMPLETED
    assert provider.model == "gpt-test"
    assert transport.payloads[0]["model"] == "gpt-test"


@pytest.mark.asyncio
async def test_stream_provider_chunks_are_normalized_into_text_and_tool_call_events():
    async def execute(args, context):
        return "lookup:{0}:{1}".format(args["query"], context.session_id)

    transport = RecordingTransport(
        [
            [
                {"choices": [{"delta": {"content": "Searching"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_lookup",
                                        "type": "function",
                                        "function": {"name": "lookup"},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '{"query":'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": ' "runtime"}'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ],
            [
                {"choices": [{"delta": {"content": "Lookup complete."}}]},
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 19},
                },
            ],
        ]
    )
    provider = OpenAICompatibleProvider(
        model="gpt-test",
        transport=transport,
        stream=True,
    )
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="lookup",
                        description="Look up project facts.",
                        input_schema={
                            "type": "object",
                            "required": ["query"],
                            "properties": {"query": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        execute=execute,
                    )
                ]
            )
        ),
        max_iterations=2,
    )

    result = await runner.run(session_id="session-stream", user_text="Use lookup.")

    assert result.status == LoopStatus.COMPLETED
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == "Lookup complete."

    history = store.read_history(result.session_id)
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert history[1].parts[0].type is MessagePartType.TEXT
    assert history[1].parts[0].text == "Searching"
    assert history[1].parts[1].type is MessagePartType.TOOL_CALL
    assert history[1].parts[1].tool_call.call_id == "call_lookup"
    assert history[1].parts[1].tool_call.tool_name == "lookup"
    assert history[1].parts[1].tool_call.arguments == {"query": "runtime"}
    assert history[2].parts[0].type is MessagePartType.TOOL_RESULT
    assert history[2].parts[0].tool_result.content == "lookup:runtime:session-stream"

    assert len(transport.payloads) == 2
    assert transport.payloads[0]["stream"] is True
    assert transport.payloads[0]["tools"][0]["function"]["name"] == "lookup"
    assert any(message["role"] == "tool" for message in transport.payloads[1]["messages"])


@pytest.mark.asyncio
async def test_transport_send_error_maps_to_loop_error_status():
    transport = RecordingTransport([RuntimeError("network disabled")])
    provider = OpenAICompatibleProvider(model="gpt-test", transport=transport)
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(session_id="session-error", user_text="Trigger error.")

    assert result.status == LoopStatus.ERROR
    assert result.final_assistant_message is not None
    error_part = result.final_assistant_message.parts[0]
    assert error_part.type is MessagePartType.ERROR
    assert "OpenAI-compatible transport failed" in error_part.text
    assert "network disabled" in error_part.text
    assert any(event.type == "llm.error" for event in result.runtime_events)


def test_provider_transport_imports_standalone_with_pythonpath_src():
    code = """
import json
import sys

import efp_runtime.llm.provider

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


def _lookup_tool() -> ToolDef:
    async def execute(args, context):
        return "unused"

    return ToolDef(
        id="lookup",
        description="Look up project facts.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
        execute=execute,
    )


def _chat_response(text):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": "stop",
            }
        ]
    }


def _responses_response(text):
    return {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    }
