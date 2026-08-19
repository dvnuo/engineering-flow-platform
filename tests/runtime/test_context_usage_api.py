import json

import pytest

from src.efp_runtime.context.usage import build_context_usage_snapshot
from src.efp_runtime.llm.request import (
    ProviderRequest,
    RequestMessage,
    RequestMessagePart,
    RequestToolCall,
    RequestToolSchema,
)
from src.efp_runtime.session.file_store import FileSessionStore
from src.efp_runtime.session.gateway_facade import RuntimeSessionArtifacts, RuntimeSessionManager
from src.efp_runtime.session.models import MessagePart


def test_rendered_request_usage_has_four_coarse_categories():
    request = ProviderRequest(
        messages=[
            RequestMessage(
                role="system",
                parts=[RequestMessagePart(type="text", text="Follow repository instructions")],
            ),
            RequestMessage(
                role="user",
                parts=[RequestMessagePart(type="text", text="Inspect the current branch")],
            ),
            RequestMessage(
                role="assistant",
                parts=[
                    RequestMessagePart(
                        type="tool_call",
                        tool_call=RequestToolCall(
                            call_id="call-1",
                            tool_name="status",
                            arguments={"short": True},
                        ),
                    )
                ],
            ),
        ],
        tools=[
            RequestToolSchema(
                id="status",
                name="status",
                description="Read repository status",
                json_schema={"type": "object"},
            )
        ],
        metadata={
            "provider_id": "github-copilot",
            "model_id": "gpt-5.4",
            "context_window_tokens": 1_000,
            "chars_per_token": 4,
        },
    )

    snapshot = build_context_usage_snapshot(request)
    categories = {item["id"]: item for item in snapshot["categories"]}

    assert set(categories) == {
        "instructions",
        "tool_definitions",
        "conversation",
        "tool_activity",
    }
    assert all(item["tokens"] > 0 for item in categories.values())
    assert snapshot["used_tokens"] == sum(item["tokens"] for item in categories.values())
    assert snapshot["usage_percent"] == round(snapshot["used_tokens"] / 10, 1)
    assert "Follow repository instructions" not in json.dumps(snapshot)


@pytest.mark.asyncio
async def test_native_context_usage_and_manual_compact_create_checkpoint(tmp_path, monkeypatch):
    from src.gateway import runtime_api

    store = FileSessionStore(tmp_path / "runtime")
    store.create_session(session_id="session-context")
    store.append_message(
        "session-context",
        role="user",
        parts=[MessagePart.text_part("First question " * 20)],
        status="complete",
    )
    store.append_message(
        "session-context",
        role="assistant",
        parts=[MessagePart.text_part("First answer " * 20)],
        status="complete",
    )
    store.append_message(
        "session-context",
        role="user",
        parts=[MessagePart.text_part("Latest question")],
        status="complete",
    )
    manager = RuntimeSessionManager(store=store)
    await manager.initialize()
    monkeypatch.setattr(runtime_api, "session_manager", manager)
    monkeypatch.setattr(
        runtime_api,
        "runtime_session_artifacts",
        RuntimeSessionArtifacts(tmp_path / "runtime"),
    )

    class Request:
        match_info = {"session_id": "session-context"}

    usage_response = await runtime_api.api_session_context_usage(Request())
    usage = json.loads(usage_response.text)
    assert usage_response.status == 200
    assert usage["success"] is True
    assert usage["usage_percent"] is not None
    assert len(usage["categories"]) == 4

    compact_response = await runtime_api.api_compact_session(Request())
    compacted = json.loads(compact_response.text)
    assert compact_response.status == 200
    assert compacted["success"] is True
    assert compacted["checkpoint_id"].startswith("checkpoint_")
    assert store.list_checkpoints("session-context")[0].checkpoint_id == compacted["checkpoint_id"]
    assert compacted["after_message_count"] < compacted["before_message_count"]
    assert compacted["after"]["scope"] == "current_estimate"
