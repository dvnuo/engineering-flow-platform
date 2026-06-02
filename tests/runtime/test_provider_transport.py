from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib import error as urllib_error

import pytest

import efp_runtime.llm.provider as provider_module
from efp_runtime.llm.provider import (
    DEFAULT_COPILOT_REASONING_EFFORT,
    GitHubCopilotHTTPTransport,
    GitHubCopilotProvider,
    OpenAICompatibleProvider,
    ProviderModelUnavailableError,
    ProviderTransportError,
    RecordingTransport,
    SUPPORTED_COPILOT_REASONING_EFFORTS,
    parse_copilot_api_base_url,
    github_copilot_provider_from_env,
)
from efp_runtime.llm.models import SUPPORTED_COPILOT_MODEL_IDS
from efp_runtime.llm.request import (
    ProviderRequest,
    RequestMessage,
    RequestMessagePart,
    RequestToolCall,
    RequestToolResult,
    RequestToolSchema,
)
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, RuntimeRequest
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
async def test_github_copilot_provider_defaults_strict_responses_payload():
    transport = RecordingTransport([_responses_response("Copilot answer.")])
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
    assert provider.model == "gpt-5.4"
    assert provider.endpoint == "responses"
    assert provider.reasoning_effort == DEFAULT_COPILOT_REASONING_EFFORT
    assert provider.metadata["provider_id"] == "github-copilot"
    payload = transport.payloads[0]
    assert payload["model"] == "gpt-5.4"
    assert "input" in payload
    assert "messages" not in payload
    assert payload["reasoning"] == {"effort": "high"}
    assert "metadata" not in payload
    assert "tools" not in payload
    input_item = payload["input"][0]["content"][0]
    assert input_item == {
        "type": "input_text",
        "text": "Answer with Copilot default.",
    }


@pytest.mark.asyncio
async def test_github_copilot_provider_requested_model_only_changes_payload_model():
    transport = RecordingTransport([_responses_response("Copilot override.")])
    provider = GitHubCopilotProvider(transport=transport)
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(
        session_id="session-copilot-model-hint",
        user_text="Answer with requested model.",
        metadata={"requested_model": "gpt-5 mini"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert provider.model == "gpt-5.4"
    assert transport.payloads[0]["model"] == "gpt-5-mini"
    assert "metadata" not in transport.payloads[0]


def test_github_copilot_provider_rejects_invalid_model_locally():
    with pytest.raises(ValueError, match="unsupported GitHub Copilot model"):
        GitHubCopilotProvider(
            transport=RecordingTransport([]),
            model="gpt-5",
        )


def test_github_copilot_provider_rejects_invalid_requested_model_locally():
    provider = GitHubCopilotProvider(transport=RecordingTransport([]))

    with pytest.raises(ValueError, match="unsupported GitHub Copilot model"):
        provider.build_payload(
            _runtime_request_with_metadata({"requested_model": "gpt-4o"})
        )


def test_github_copilot_provider_rejects_invalid_reasoning_effort_locally():
    with pytest.raises(ValueError, match="unsupported GitHub Copilot reasoning effort"):
        GitHubCopilotProvider(
            transport=RecordingTransport([]),
            reasoning_effort="extreme",
        )


def test_github_copilot_supported_models_and_reasoning_are_exact():
    assert SUPPORTED_COPILOT_MODEL_IDS == (
        "gpt-5-mini",
        "gpt-5.3-codex",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.5",
        "gemini-2.5-pro",
        "gemini-3.5-flash",
    )
    assert SUPPORTED_COPILOT_REASONING_EFFORTS == (
        "low",
        "medium",
        "high",
        "xhigh",
    )


def test_github_copilot_injects_noop_when_tool_history_exists_without_tools():
    provider = GitHubCopilotProvider(transport=RecordingTransport([]))

    payload = provider.build_payload(_runtime_request_with_tool_history())

    assert [tool["name"] for tool in payload["tools"]] == ["_noop"]
    assert "metadata" not in payload


def test_openai_provider_does_not_inject_noop_for_tool_history_without_tools():
    provider = OpenAICompatibleProvider(
        model="gpt-test",
        transport=RecordingTransport([]),
    )

    payload = provider.build_payload(_runtime_request_with_tool_history())

    assert payload["tools"] == []
    assert "copilot_noop_tool_fallback" not in payload["metadata"]


def test_github_copilot_does_not_inject_noop_when_real_tools_exist():
    provider = GitHubCopilotProvider(transport=RecordingTransport([]))
    schema = RequestToolSchema(
        id="lookup",
        name="lookup",
        description="Look up project facts.",
        json_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    )

    payload = provider.build_payload(_runtime_request_with_tool_history(tools=[schema]))

    assert [tool["name"] for tool in payload["tools"]] == ["lookup"]
    assert "metadata" not in payload


def test_github_copilot_tool_history_uses_top_level_response_items():
    provider = GitHubCopilotProvider(transport=RecordingTransport([]))

    payload = provider.build_payload(
        _runtime_request_with_tool_call_and_result_history()
    )

    assert payload["input"] == [
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Checking the index."}],
        },
        {
            "type": "function_call",
            "call_id": "call_lookup",
            "name": "lookup",
            "arguments": '{"query":"runtime"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_lookup",
            "output": "found runtime notes",
        },
    ]
    for input_item in payload["input"]:
        assert "metadata" not in input_item
        assert "tool_name" not in input_item
        assert "arguments_json" not in input_item
        assert "arguments_text" not in input_item
        assert "raw" not in input_item
        assert "created_at" not in input_item
        assert "status" not in input_item
        for content_item in input_item.get("content", []):
            assert set(content_item) <= {
                "type",
                "text",
                "image_url",
                "file_id",
                "filename",
                "file_data",
            }
            assert content_item["type"] != "function_call"
            assert content_item["type"] != "function_call_output"


@pytest.mark.asyncio
async def test_github_copilot_transport_shortens_long_responses_call_ids():
    long_call_id = "call_" + ("copilot_raw_tool_call_id_" * 18)
    transport = RecordingTransport([_responses_response("Done.")])
    provider = GitHubCopilotProvider(transport=transport)

    await provider.invoke(
        _runtime_request_with_tool_call_and_result_history(call_id=long_call_id)
    )

    payload = transport.payloads[0]
    call_items = [
        item
        for item in payload["input"]
        if item.get("type") in {"function_call", "function_call_output"}
    ]
    call_ids = _collect_call_ids(payload)
    shortened_call_id = call_items[0]["call_id"]

    assert len(long_call_id) > 64
    assert [item["type"] for item in call_items] == [
        "function_call",
        "function_call_output",
    ]
    assert call_items[0]["call_id"] == call_items[1]["call_id"]
    assert shortened_call_id.startswith("call_")
    assert len(shortened_call_id) <= 64
    assert shortened_call_id != long_call_id
    assert long_call_id not in call_ids
    assert all(len(call_id) <= 64 for call_id in call_ids)


def test_copilot_sanitizer_shortens_long_call_ids_for_bypassed_projection():
    long_call_id = "call_" + ("manual_responses_call_id_" * 18)

    payload = provider_module._sanitize_copilot_responses_payload(
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": long_call_id,
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": long_call_id,
                    "output": "ok",
                },
                {
                    "type": "function_call",
                    "call_id": "call_short",
                    "name": "lookup",
                    "arguments": "{}",
                },
            ],
        }
    )

    long_call_item = payload["input"][0]
    long_result_item = payload["input"][1]
    short_call_item = payload["input"][2]

    assert long_call_item["call_id"] == long_result_item["call_id"]
    assert long_call_item["call_id"].startswith("call_")
    assert len(long_call_item["call_id"]) <= 64
    assert long_call_item["call_id"] != long_call_id
    assert short_call_item["call_id"] == "call_short"
    assert all(len(call_id) <= 64 for call_id in _collect_call_ids(payload))


def test_copilot_sanitizer_uses_output_text_for_bypassed_assistant_text():
    payload = provider_module._sanitize_copilot_responses_payload(
        {
            "input": [
                {"role": "assistant", "content": "Plain assistant text."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "input_text", "text": "Legacy assistant input."},
                        {"type": "output_text", "text": "Assistant output."},
                        {"type": "text", "text": "Plain item."},
                        {
                            "type": "input_image",
                            "image_url": {"url": "https://example.test/i.png"},
                        },
                        {"type": "input_file", "file_id": "file_123"},
                        {"type": "refusal", "refusal": "Cannot comply."},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "output_text", "text": "User text."},
                        {
                            "type": "input_image",
                            "image_url": {"url": "https://example.test/u.png"},
                        },
                        {"type": "input_file", "file_id": "file_456"},
                    ],
                },
            ],
        }
    )

    assert payload["input"][0]["content"] == [
        {"type": "output_text", "text": "Plain assistant text."}
    ]
    assert payload["input"][1]["content"] == [
        {"type": "output_text", "text": "Legacy assistant input."},
        {"type": "output_text", "text": "Assistant output."},
        {"type": "output_text", "text": "Plain item."},
        {"type": "refusal", "refusal": "Cannot comply."},
    ]
    assert all(
        content_item["type"] != "input_text"
        for item in payload["input"]
        if item.get("role") == "assistant"
        for content_item in item.get("content", [])
    )
    assert payload["input"][2]["content"] == [
        {"type": "input_text", "text": "User text."},
        {"type": "input_image", "image_url": {"url": "https://example.test/u.png"}},
        {"type": "input_file", "file_id": "file_456"},
    ]


@pytest.mark.asyncio
async def test_provider_returned_noop_tool_call_is_ignored_without_execution():
    executions = []

    async def execute(args, context):
        executions.append(args)
        return "side effect"

    transport = RecordingTransport(
        [
            _responses_tool_call_response(
                call_id="call_noop",
                tool_name="_noop",
                arguments="{}",
            ),
            _responses_response("Done."),
        ]
    )
    provider = GitHubCopilotProvider(transport=transport)
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="side_effect",
                        description="Records execution.",
                        input_schema={"type": "object", "properties": {}},
                        execute=execute,
                    )
                ]
            )
        ),
        max_iterations=2,
    )

    result = await runner.run(session_id="session-noop", user_text="Continue.")

    assert result.status == LoopStatus.COMPLETED
    assert executions == []
    assert "_noop" not in runner.tool_runtime.registry.ids()
    history = store.read_history("session-noop")
    noop_result = history[2].parts[0].tool_result
    assert noop_result.tool_name == "_noop"
    assert noop_result.status == "ignored"
    assert noop_result.metadata["noop_fallback"] is True


@pytest.mark.asyncio
async def test_github_copilot_model_unavailable_does_not_fallback_to_gpt_5_5():
    transport = RecordingTransport(
        [
            ProviderModelUnavailableError(
                "The requested model is not available. Available models: [gpt-5.5 gpt-5-mini]",
                status_code=400,
                available_models_text="[gpt-5.5 gpt-5-mini]",
            )
        ]
    )
    provider = GitHubCopilotProvider(transport=transport, model="gpt-5.4")
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(
        session_id="session-copilot-model-unavailable",
        user_text="Do not retry.",
    )

    assert result.status == LoopStatus.ERROR
    assert len(transport.payloads) == 1
    assert transport.payloads[0]["model"] == "gpt-5.4"
    _assert_strict_copilot_responses_payload(transport.payloads[0])


@pytest.mark.asyncio
async def test_github_copilot_regular_http_400_does_not_fallback():
    transport = RecordingTransport([ProviderTransportError("HTTP 400 bad request")])
    provider = GitHubCopilotProvider(transport=transport, model="gpt-5.4")
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(
        session_id="session-copilot-regular-400",
        user_text="Do not fallback.",
    )

    assert result.status == LoopStatus.ERROR
    assert len(transport.payloads) == 1
    assert transport.payloads[0]["model"] == "gpt-5.4"
    _assert_strict_copilot_responses_payload(transport.payloads[0])


@pytest.mark.asyncio
async def test_github_copilot_http_transport_posts_json_headers_and_returns_raw(
    monkeypatch,
):
    requests = []
    raw_response = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "HTTP answer."}],
            }
        ]
    }

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeHTTPResponse(raw_response)

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)

    transport = GitHubCopilotHTTPTransport(
        token="secret-token",
        timeout=12,
    )
    payload = {
        "model": "gpt-5-mini",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Say ok"}],
            }
        ],
        "reasoning": {"effort": "high"},
        "stream": False,
    }

    result = await transport.send(payload)

    assert result == raw_response
    assert len(requests) == 1
    request, timeout = requests[0]
    assert timeout == 12
    assert request.full_url == "https://api.githubcopilot.com/responses"
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8")) == payload

    headers = _request_headers(request)
    assert headers["authorization"] == "Bearer secret-token"
    assert headers["content-type"] == "application/json"
    assert headers["accept"] == "application/vnd.github.copilot-chat-preview+json"
    assert headers["user-agent"] == "GitHubCopilotChat/0.35.0"
    assert headers["editor-version"] == "vscode/1.107.0"
    assert headers["editor-plugin-version"] == "copilot-chat/0.35.0"
    assert headers["copilot-integration-id"] == "vscode-chat"
    assert headers["openai-intent"] == "conversation-edits"
    assert headers["x-initiator"] == "agent"


def test_parse_copilot_api_base_url_maps_proxy_endpoint_hosts():
    assert (
        parse_copilot_api_base_url(
            "token;proxy-ep=https%3A%2F%2Fproxy.individual.githubcopilot.com;sku=free"
        )
        == "https://api.individual.githubcopilot.com"
    )
    assert (
        parse_copilot_api_base_url(
            "token proxy-ep=https%3A%2F%2Fproxy.enterprise.githubcopilot.com%2Fv1"
        )
        == "https://api.enterprise.githubcopilot.com"
    )


@pytest.mark.asyncio
async def test_github_source_token_exchange_uses_copilot_plugin_headers_and_parsed_base_url(
    monkeypatch,
):
    requests = []
    exchanged_token = (
        "copilot-token;proxy-ep=https%3A%2F%2Fproxy.individual.githubcopilot.com;sku=free"
    )

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        if request.get_method() == "GET":
            return _FakeHTTPResponse({"token": exchanged_token, "expires_at": 1893456000})
        return _FakeHTTPResponse(_responses_response("HTTP answer."))

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)

    transport = GitHubCopilotHTTPTransport(token="ghp_source123", timeout=12)
    result = await transport.send(
        {
            "model": "gpt-5.4",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "ok"}]}],
            "reasoning": {"effort": "high"},
            "stream": False,
        }
    )

    assert result == _responses_response("HTTP answer.")
    assert transport.token_source == "github_exchange"
    assert transport.token_expires_at == 1893456000
    assert transport.endpoint == "https://api.individual.githubcopilot.com/responses"
    assert len(requests) == 2

    exchange_request, exchange_timeout = requests[0]
    assert exchange_timeout == 12
    assert exchange_request.full_url == (
        "https://api.github.com/copilot_internal/v2/token"
    )
    assert exchange_request.get_method() == "GET"
    exchange_headers = _request_headers(exchange_request)
    assert exchange_headers["authorization"] == "Bearer ghp_source123"
    assert exchange_headers["accept"] == "application/json"
    assert exchange_headers["user-agent"] == "GitHubCopilotChat/0.35.0"
    assert exchange_headers["editor-version"] == "vscode/1.107.0"
    assert exchange_headers["editor-plugin-version"] == "copilot-chat/0.35.0"
    assert exchange_headers["copilot-integration-id"] == "vscode-chat"
    assert "openai-intent" not in exchange_headers
    assert "x-initiator" not in exchange_headers

    response_request, _ = requests[1]
    assert response_request.full_url == (
        "https://api.individual.githubcopilot.com/responses"
    )
    response_headers = _request_headers(response_request)
    assert response_headers["authorization"] == "Bearer {0}".format(exchanged_token)
    assert response_headers["accept"] == (
        "application/vnd.github.copilot-chat-preview+json"
    )
    assert response_headers["openai-intent"] == "conversation-edits"
    assert response_headers["x-initiator"] == "agent"


def test_github_source_token_exchange_keeps_explicit_base_url(monkeypatch):
    requests = []
    exchanged_token = (
        "copilot-token;proxy-ep=https%3A%2F%2Fproxy.enterprise.githubcopilot.com"
    )

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeHTTPResponse({"token": exchanged_token, "expires_at": 1893456000})

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)

    transport = GitHubCopilotHTTPTransport(
        token="github_pat_source123",
        base_url="https://configured-copilot.example/",
        timeout=12,
    )

    assert len(requests) == 1
    assert requests[0][0].full_url == "https://api.github.com/copilot_internal/v2/token"
    assert transport.endpoint == "https://configured-copilot.example/responses"
    assert transport._headers()["Authorization"] == "Bearer {0}".format(
        exchanged_token
    )


@pytest.mark.asyncio
async def test_github_copilot_http_transport_rejects_stream_without_network(
    monkeypatch,
):
    called = False

    def fake_urlopen(request, timeout):
        nonlocal called
        called = True
        return _FakeHTTPResponse({})

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)

    transport = GitHubCopilotHTTPTransport(token="secret-token")
    with pytest.raises(ProviderTransportError, match="does not support streaming"):
        await transport.send({"model": "gpt-5-mini", "stream": True})

    assert called is False


@pytest.mark.asyncio
async def test_github_copilot_http_transport_errors_do_not_leak_token(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib_error.HTTPError(
            request.full_url,
            401,
            "Unauthorized secret-token",
            hdrs=None,
            fp=io.BytesIO(b'{"error": "bad secret-token"}'),
        )

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)

    transport = GitHubCopilotHTTPTransport(token="secret-token")
    with pytest.raises(ProviderTransportError) as exc_info:
        await transport.send(
            {
                "model": "gpt-5-mini",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Say ok"}],
                    }
                ],
                "reasoning": {"effort": "high"},
                "stream": False,
            }
        )

    message = str(exc_info.value)
    assert "401" in message
    assert "[redacted]" in message
    assert "secret-token" not in message
    assert exc_info.value.__cause__ is None


@pytest.mark.asyncio
async def test_github_copilot_http_transport_raises_model_unavailable(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib_error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(
                b'{"error":{"message":"The requested model is not available for integrator secret-token. Available models: [gpt-5.5 gpt-5-mini]"}}'
            ),
        )

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)

    transport = GitHubCopilotHTTPTransport(token="secret-token")
    with pytest.raises(ProviderModelUnavailableError) as exc_info:
        await transport.send(
            {
                "model": "gpt-5.4",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Say ok"}],
                    }
                ],
                "reasoning": {"effort": "high"},
                "stream": False,
            }
        )

    error = exc_info.value
    assert error.status_code == 400
    assert error.available_models_text == "[gpt-5.5 gpt-5-mini]"
    assert "requested model is not available" in str(error)
    assert "[redacted]" in str(error)
    assert "secret-token" not in str(error)


@pytest.mark.asyncio
async def test_github_copilot_http_transport_regular_400_is_transport_error(
    monkeypatch,
):
    def fake_urlopen(request, timeout):
        raise urllib_error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"invalid request"}}'),
        )

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)

    transport = GitHubCopilotHTTPTransport(token="secret-token")
    with pytest.raises(ProviderTransportError) as exc_info:
        await transport.send(
            {
                "model": "gpt-5.4",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Say ok"}],
                    }
                ],
                "reasoning": {"effort": "high"},
                "stream": False,
            }
        )

    assert not isinstance(exc_info.value, ProviderModelUnavailableError)
    assert "with status 400" in str(exc_info.value)
    assert "invalid request" in str(exc_info.value)


def test_github_copilot_provider_from_env_reads_token_and_base_url():
    provider = github_copilot_provider_from_env(
        env={
            "EFP_GITHUB_COPILOT_TOKEN": " efp-token ",
            "GITHUB_COPILOT_TOKEN": "fallback-token",
            "EFP_GITHUB_COPILOT_BASE_URL": "https://copilot-api.enterprise.example/",
            "EFP_GITHUB_COPILOT_REASONING_EFFORT": " medium ",
        }
    )

    assert provider.model == "gpt-5.4"
    assert provider.endpoint == "responses"
    assert provider.reasoning_effort == "medium"
    assert provider.metadata["provider_id"] == "github-copilot"
    assert isinstance(provider.transport, GitHubCopilotHTTPTransport)
    assert (
        provider.transport.endpoint
        == "https://copilot-api.enterprise.example/responses"
    )
    assert provider.transport._headers()["Authorization"] == "Bearer efp-token"
    assert provider.transport._headers()["x-initiator"] == "agent"


def test_github_copilot_provider_from_env_falls_back_to_github_token():
    provider = github_copilot_provider_from_env(
        model="gemini 3.5 flash",
        env={"GITHUB_COPILOT_TOKEN": "github-token"},
    )

    assert provider.model == "gemini-3.5-flash"
    assert isinstance(provider.transport, GitHubCopilotHTTPTransport)
    assert provider.transport._headers()["Authorization"] == "Bearer github-token"


def test_github_copilot_provider_from_env_exchanges_github_source_token(monkeypatch):
    requests = []
    exchanged_token = (
        "copilot-token;proxy-ep=https%3A%2F%2Fproxy.enterprise.githubcopilot.com"
    )

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeHTTPResponse({"token": exchanged_token, "expires_at": 1893456000})

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)

    provider = github_copilot_provider_from_env(
        env={"EFP_GITHUB_COPILOT_TOKEN": "ghu_source123"}
    )

    assert provider.model == "gpt-5.4"
    assert isinstance(provider.transport, GitHubCopilotHTTPTransport)
    assert provider.transport.token_source == "github_exchange"
    assert provider.transport.endpoint == "https://api.enterprise.githubcopilot.com/responses"
    assert provider.transport._headers()["Authorization"] == "Bearer {0}".format(
        exchanged_token
    )
    assert requests[0][0].full_url == "https://api.github.com/copilot_internal/v2/token"


def test_github_copilot_provider_from_env_requires_token():
    with pytest.raises(ProviderTransportError) as exc_info:
        github_copilot_provider_from_env(env={})

    message = str(exc_info.value)
    assert "EFP_GITHUB_COPILOT_TOKEN" in message
    assert "GITHUB_COPILOT_TOKEN" in message


def test_github_copilot_smoke_dry_run_outputs_payload_without_token():
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env.pop("EFP_GITHUB_COPILOT_TOKEN", None)
    env.pop("GITHUB_COPILOT_TOKEN", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "efp_runtime.smoke.github_copilot",
            "--dry-run",
            "--prompt",
            "Say ok",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["provider"] == "github-copilot"
    assert payload["provider_id"] == "github-copilot"
    assert payload["model"] == "gpt-5.4"
    assert payload["payload_summary"]["tool_count"] == 0
    assert payload["payload_summary"]["stream"] is False
    assert payload["payload_summary"]["reasoning"] == {"effort": "high"}
    assert payload["payload"]["model"] == "gpt-5.4"
    assert payload["payload"]["reasoning"] == {"effort": "high"}
    assert payload["payload"]["input"][-1]["role"] == "user"
    input_item = payload["payload"]["input"][-1]["content"][0]
    assert input_item == {"type": "input_text", "text": "Say ok"}
    assert "messages" not in payload["payload"]
    assert "metadata" not in payload["payload"]
    assert "metadata" not in payload["payload_summary"]
    assert "tools" not in payload["payload"]
    assert "Authorization" not in result.stdout
    assert "metadata" not in result.stdout


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


def _collect_call_ids(value):
    if isinstance(value, dict):
        collected = []
        call_id = value.get("call_id")
        if isinstance(call_id, str):
            collected.append(call_id)
        for nested in value.values():
            collected.extend(_collect_call_ids(nested))
        return collected
    if isinstance(value, list):
        collected = []
        for nested in value:
            collected.extend(_collect_call_ids(nested))
        return collected
    return []


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


def _runtime_request_with_tool_history(
    *,
    tools: list[RequestToolSchema] | None = None,
) -> RuntimeRequest:
    return RuntimeRequest(
        session_id="session-history",
        messages=[],
        iteration=1,
        max_iterations=1,
        provider_request=ProviderRequest(
            messages=[
                RequestMessage(
                    role="assistant",
                    parts=[
                        RequestMessagePart(
                            type="tool_call",
                            tool_call=RequestToolCall(
                                call_id="call_lookup",
                                tool_name="lookup",
                                arguments={"query": "runtime"},
                            ),
                        )
                    ],
                )
            ],
            tools=list(tools or []),
        ),
    )


def _runtime_request_with_tool_call_and_result_history(
    *,
    call_id: str = "call_lookup",
) -> RuntimeRequest:
    return RuntimeRequest(
        session_id="session-tool-history",
        messages=[],
        iteration=1,
        max_iterations=1,
        provider_request=ProviderRequest(
            messages=[
                RequestMessage(
                    role="assistant",
                    parts=[
                        RequestMessagePart(
                            type="text",
                            text="Checking the index.",
                        ),
                        RequestMessagePart(
                            type="tool_call",
                            tool_call=RequestToolCall(
                                call_id=call_id,
                                tool_name="lookup",
                                arguments={"query": "runtime"},
                                arguments_text='{"query":"runtime"}',
                                status="completed",
                                raw={"provider": "internal"},
                                metadata={"source": "assistant"},
                                created_at="2026-01-01T00:00:00Z",
                            ),
                        ),
                    ],
                ),
                RequestMessage(
                    role="tool",
                    parts=[
                        RequestMessagePart(
                            type="tool_result",
                            tool_result=RequestToolResult(
                                call_id=call_id,
                                tool_name="lookup",
                                content="found runtime notes",
                                output={"matches": 1},
                                status="success",
                                metadata={"source": "tool"},
                                created_at="2026-01-01T00:00:01Z",
                            ),
                        )
                    ],
                ),
            ],
        ),
    )


def _runtime_request_with_metadata(metadata: dict[str, object]) -> RuntimeRequest:
    return RuntimeRequest(
        session_id="session-metadata",
        messages=[],
        iteration=1,
        max_iterations=1,
        provider_request=ProviderRequest(
            messages=[
                RequestMessage(
                    role="user",
                    parts=[
                        RequestMessagePart(
                            type="text",
                            text="Hello",
                        )
                    ],
                )
            ],
        ),
        metadata=dict(metadata),
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


def _chat_tool_call_response(*, call_id: str, tool_name: str, arguments: str):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": arguments,
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _responses_tool_call_response(*, call_id: str, tool_name: str, arguments: str):
    return {
        "output": [
            {
                "type": "function_call",
                "call_id": call_id,
                "name": tool_name,
                "arguments": arguments,
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


def _assert_strict_copilot_responses_payload(payload):
    assert "input" in payload
    assert "messages" not in payload
    assert "metadata" not in payload
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["stream"] is False


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


def _request_headers(request):
    headers = {key.lower(): value for key, value in request.header_items()}
    for source in (request.headers, request.unredirected_hdrs):
        headers.update({key.lower(): value for key, value in source.items()})
    return headers
