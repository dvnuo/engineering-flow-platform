"""Copilot provider: tool_choice on the summary turn, reasoning replay, x-initiator, catalog prices."""

from __future__ import annotations

import pytest

from efp_runtime.llm.pricing import clear_catalog_pricing, pricing_for_model
from efp_runtime.llm.provider import (
    GitHubCopilotHTTPTransport,
    GitHubCopilotProvider,
    _copilot_initiator_for,
    _sanitize_copilot_responses_payload,
)
from efp_runtime.llm.request import (
    PreparedProviderRequest,
    ProviderRequest,
    RequestMessage,
    RequestMessagePart,
    RequestToolSchema,
)
from efp_runtime.loop.provider import RuntimeRequest


TOOL = {
    "type": "function",
    "name": "search",
    "description": "Search",
    "parameters": {"type": "object", "properties": {}},
}


def _runtime_request(metadata=None, *, iteration: int = 1, tools: bool = True) -> RuntimeRequest:
    provider_request = ProviderRequest(
        messages=[RequestMessage(role="user", parts=[RequestMessagePart(type="text", text="hi")])],
        tools=(
            [
                RequestToolSchema(
                    id="search",
                    name="search",
                    description="Search",
                    json_schema={"type": "object", "properties": {}},
                )
            ]
            if tools
            else []
        ),
    )
    return RuntimeRequest(
        session_id="s",
        messages=[],
        iteration=iteration,
        max_iterations=None,
        metadata=dict(metadata or {}),
        provider_request=provider_request,
        prepared_request=PreparedProviderRequest(request=provider_request),
    )


def _assistant_with_reasoning(encrypted="enc-1", item_id="rs_1", follow="call") -> dict:
    part_metadata = {
        "item_id": item_id,
        "provider_metadata": {"openai": {"itemId": item_id}},
    }
    if encrypted is not None:
        part_metadata["encrypted_content"] = encrypted
        part_metadata["provider_metadata"]["openai"]["reasoningEncryptedContent"] = encrypted
    content = [{"type": "reasoning", "text": "thinking", "metadata": {"part_metadata": part_metadata}}]
    if follow == "call":
        content.append({"type": "function_call", "call_id": "call_1", "name": "search", "arguments": "{}"})
    elif follow == "text":
        content.append({"type": "output_text", "text": "answer"})
    return {"role": "assistant", "content": content}


def test_sanitizer_keeps_tool_choice_include_and_store():
    payload = {
        "model": "gpt-6-sol",
        "input": [{"role": "user", "content": "hi"}],
        "tools": [TOOL],
        "tool_choice": "none",
        "include": ["reasoning.encrypted_content"],
        "store": False,
        "metadata": {"x": 1},
    }
    sanitized = _sanitize_copilot_responses_payload(payload)
    assert sanitized["tool_choice"] == "none"
    assert sanitized["include"] == ["reasoning.encrypted_content"]
    assert sanitized["store"] is False
    assert "metadata" not in sanitized

    no_tools = _sanitize_copilot_responses_payload({"model": "gpt-6-sol", "input": [], "tool_choice": "none"})
    assert "tool_choice" not in no_tools
    odd = _sanitize_copilot_responses_payload({"model": "gpt-6-sol", "tools": [TOOL], "tool_choice": "weird"})
    assert "tool_choice" not in odd
    named = _sanitize_copilot_responses_payload(
        {"model": "gpt-6-sol", "tools": [TOOL], "tool_choice": {"type": "function", "name": "search", "extra": 1}}
    )
    assert named["tool_choice"] == {"type": "function", "name": "search"}


def test_reasoning_items_are_replayed_only_when_asked_and_followed():
    payload = {"model": "gpt-6-sol", "input": [_assistant_with_reasoning()]}
    off = _sanitize_copilot_responses_payload(payload)
    assert [item.get("type", "message") for item in off["input"]] == ["function_call"]

    on = _sanitize_copilot_responses_payload(payload, replay_reasoning=True)
    assert [item.get("type", "message") for item in on["input"]] == ["reasoning", "function_call"]
    assert on["input"][0] == {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "enc-1",
    }

    text = _sanitize_copilot_responses_payload(
        {"model": "gpt-6-sol", "input": [_assistant_with_reasoning(follow="text")]},
        replay_reasoning=True,
    )
    assert [item.get("type", "message") for item in text["input"]] == ["reasoning", "message"]
    assert text["input"][1]["content"] == [{"type": "output_text", "text": "answer"}]

    dangling = _sanitize_copilot_responses_payload(
        {"model": "gpt-6-sol", "input": [_assistant_with_reasoning(follow=None)]},
        replay_reasoning=True,
    )
    assert dangling["input"] == []

    plain = _sanitize_copilot_responses_payload(
        {"model": "gpt-6-sol", "input": [_assistant_with_reasoning(encrypted=None)]},
        replay_reasoning=True,
    )
    assert [item.get("type", "message") for item in plain["input"]] == ["function_call"]


def test_build_payload_sends_tool_choice_and_replay_flags():
    transport = GitHubCopilotHTTPTransport(token="efp-token")
    provider = GitHubCopilotProvider(transport=transport, model="gpt-6-sol", replay_reasoning=True)
    payload = provider.build_payload(_runtime_request({"tool_choice": "none"}))
    assert payload["tool_choice"] == "none"
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "medium"}

    plain = GitHubCopilotProvider(transport=transport, model="gpt-6-sol").build_payload(_runtime_request())
    assert "tool_choice" not in plain
    assert "include" not in plain
    assert "store" not in plain

    no_tools = GitHubCopilotProvider(transport=transport, model="gpt-6-sol").build_payload(
        _runtime_request({"tool_choice": "none"}, tools=False)
    )
    assert "tool_choice" not in no_tools


def test_initiator_marks_the_first_call_of_a_turn_user_and_follow_ups_agent():
    assert _copilot_initiator_for(_runtime_request(iteration=1)) == "user"
    assert _copilot_initiator_for(_runtime_request(iteration=2)) == "agent"
    assert _copilot_initiator_for(_runtime_request({"task_type": "session_title"}, iteration=1)) == "agent"
    assert _copilot_initiator_for(_runtime_request({"background": True}, iteration=1)) == "agent"
    assert _copilot_initiator_for(_runtime_request({"copilot_initiator": "agent"}, iteration=1)) == "agent"
    assert _copilot_initiator_for(_runtime_request({"copilot_initiator": "user"}, iteration=3)) == "user"

    transport = GitHubCopilotHTTPTransport(token="efp-token")
    assert transport._headers()["x-initiator"] == "agent"
    transport.set_initiator("user")
    assert transport._headers()["x-initiator"] == "user"
    with pytest.raises(ValueError):
        transport.set_initiator("")


class _FakeTransport:
    def __init__(self) -> None:
        self.initiators: list[str] = []
        self.payloads: list[dict] = []

    def set_initiator(self, value: str) -> None:
        self.initiators.append(value)

    def send(self, payload):
        self.payloads.append(payload)
        return {"content": "ok"}


@pytest.mark.asyncio
async def test_invoke_sets_the_initiator_per_request():
    transport = _FakeTransport()
    provider = GitHubCopilotProvider(transport=transport, model="gpt-6-sol")

    await provider.invoke(_runtime_request(iteration=1))
    await provider.invoke(_runtime_request(iteration=2))

    assert transport.initiators == ["user", "agent"]
    assert [payload["model"] for payload in transport.payloads] == ["gpt-6-sol", "gpt-6-sol"]


def test_build_payload_registers_catalog_prices_already_fetched():
    class _CatalogTransport(_FakeTransport):
        def cached_models_catalog(self):
            return {
                "gpt-6-sol": {
                    "id": "gpt-6-sol",
                    "billing": {
                        "token_prices": {
                            "default": {"max_prompt_tokens": 272_000, "input_price": 250, "output_price": 1100},
                            "long_context": {"max_prompt_tokens": 872_000, "input_price": 500, "output_price": 1600},
                        }
                    },
                }
            }

    clear_catalog_pricing()
    try:
        GitHubCopilotProvider(transport=_CatalogTransport(), model="gpt-6-sol").build_payload(_runtime_request())
        row = pricing_for_model("gpt-6-sol")
        assert row is not None
        assert row.source == "catalog"
        assert row.default.input_usd_per_1m == pytest.approx(2.5)
        assert row.long_context is not None
        assert row.long_context.output_usd_per_1m == pytest.approx(16.0)
    finally:
        clear_catalog_pricing()
    assert pricing_for_model("gpt-6-sol").source == "static"
