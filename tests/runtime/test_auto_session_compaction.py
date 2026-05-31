from __future__ import annotations

from copy import deepcopy

import pytest

from efp_runtime.event_bus import RuntimeEventBus
from efp_runtime.llm.errors import ProviderContextOverflowError
from efp_runtime.loop import (
    LoopStatus,
    RuntimeLoopRunner,
    RuntimeRequest,
    ScriptedLLMProvider,
)
from efp_runtime.models import Attachment, Message, MessagePart, MessagePartType, MessageRole
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


class SequenceProvider:
    def __init__(self, steps):
        self.steps = list(steps)
        self.requests: list[RuntimeRequest] = []
        self.metadata_snapshots: list[dict] = []

    async def invoke(self, request: RuntimeRequest):
        self.requests.append(request)
        self.metadata_snapshots.append(deepcopy(request.provider_request.metadata))
        if not self.steps:
            raise AssertionError("SequenceProvider has no step left")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _runner(store, provider, **kwargs) -> RuntimeLoopRunner:
    return RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        **kwargs,
    )


def _seed_history(store, session_id: str, *, latest: str | None = None) -> None:
    store.create_session(session_id=session_id)
    for index, (role, text) in enumerate(
        [
            (MessageRole.USER, "old request one"),
            (MessageRole.ASSISTANT, "old answer one"),
            (MessageRole.USER, "old request two"),
            (MessageRole.ASSISTANT, "old answer two"),
        ]
    ):
        store.append_message(
            session_id,
            role=role,
            parts=[MessagePart.text_part(text)],
            message_id=f"msg-old-{index}",
            status="complete",
        )
    if latest is not None:
        store.append_message(
            session_id,
            role=MessageRole.USER,
            parts=[MessagePart.text_part(latest)],
            message_id="msg-latest-user",
            status="complete",
        )


def _compaction_parts(messages):
    return [
        part
        for message in messages
        for part in message.parts
        if part.type is MessagePartType.COMPACTION
    ]


def _replay_messages(messages):
    return [
        message
        for message in messages
        if message.metadata.get("source") == "compaction.replay"
    ]


def _events(result, event_type: str):
    return [event for event in result.runtime_events if event.type == event_type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"compaction_tail_turns": -1},
        {"compaction_preserve_recent_chars": -1},
        {"compaction_reserved_chars": -1},
    ],
)
def test_runtime_config_validates_auto_compaction_integers(kwargs):
    with pytest.raises(ValueError):
        RuntimeConfig(**kwargs)


@pytest.mark.asyncio
async def test_auto_compaction_persists_before_provider_and_request_includes_latest():
    store = InMemorySessionStore()
    _seed_history(store, "session-auto")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    bus = RuntimeEventBus()
    runner = _runner(
        store,
        provider,
        max_context_parts=3,
        compaction_tail_turns=1,
        event_bus=bus,
    )

    result = await runner.run(
        session_id="session-auto",
        user_text="latest request",
        metadata={"run_id": "run-auto"},
    )

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    request_compactions = _compaction_parts(request.messages)
    assert len(request_compactions) == 1
    assert request_compactions[0].compaction is not None
    assert request_compactions[0].compaction.auto is True
    assert request_compactions[0].compaction.overflow is False
    assert request_compactions[0].compaction.metadata["trigger"] == "context_budget"
    assert request.provider_request.messages[0].context[0].type == "compaction_summary"
    assert request.provider_request.messages[-1].role == "user"
    assert request.provider_request.messages[-1].text == "latest request"

    stored_compactions = _compaction_parts(store.read_history("session-auto"))
    assert len(stored_compactions) == 1
    assert stored_compactions[0].compaction.metadata["auto"] is True
    assert _replay_messages(store.read_history("session-auto")) == []
    assert "compaction_replay" not in request.provider_request.metadata

    started = _events(result, "session_compaction_started")
    compacted = _events(result, "session_compacted")
    assert len(started) == 1
    assert len(compacted) == 1
    for event in [started[0], compacted[0]]:
        assert event.payload["run_id"] == "run-auto"
        assert event.payload["iteration"] == 1
        assert event.payload["trigger"] == "context_budget"
        assert event.payload["auto"] is True
        assert event.payload["overflow"] is False
        assert event.payload["max_parts"] == 3
        assert event.payload["max_chars"] is None
        assert event.payload["reserve_chars"] == 0
        assert event.payload["compacted_part_count"] > 0
        assert event.payload["compacted_message_count"] > 0
    assert [event.type for event in bus.history("session-auto")[:2]] == [
        "run_start",
        "session_compaction_started",
    ]


@pytest.mark.asyncio
async def test_token_budget_converts_to_char_budget_for_auto_compaction():
    store = InMemorySessionStore()
    _seed_history(store, "session-token-budget")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(
        store,
        provider,
        max_context_tokens=20,
        context_reserve_tokens=5,
        compaction_preserve_recent_tokens=20,
        compaction_tail_turns=1,
    )

    result = await runner.run(
        session_id="session-token-budget",
        user_text="latest request",
        metadata={"run_id": "run-token-budget"},
    )

    assert result.status == LoopStatus.COMPLETED
    request_metadata = provider.requests[0].provider_request.metadata
    assert request_metadata["provider_id"] == "github-copilot"
    assert request_metadata["model_id"] == "gpt-5-mini"
    assert request_metadata["context_window_tokens"] == 128_000
    assert request_metadata["max_context_tokens"] == 20
    assert request_metadata["max_context_chars"] == 80
    assert request_metadata["context_reserve_tokens"] == 5
    assert request_metadata["context_reserve_chars"] == 20
    assert request_metadata["compaction_preserve_recent_tokens"] == 20
    assert request_metadata["compaction_preserve_recent_chars"] == 80
    assert request_metadata["context_budget"]["max_context_chars_source"] == (
        "max_context_tokens"
    )
    stored_compactions = _compaction_parts(store.read_history("session-token-budget"))
    assert len(stored_compactions) == 1
    compaction_metadata = stored_compactions[0].compaction.metadata
    assert compaction_metadata["max_context_tokens"] == 20
    assert compaction_metadata["max_context_chars"] == 80
    assert compaction_metadata["context_reserve_tokens"] == 5
    assert compaction_metadata["context_reserve_chars"] == 20
    assert compaction_metadata["compaction_preserve_recent_tokens"] == 20
    assert compaction_metadata["compaction_preserve_recent_chars"] == 80


@pytest.mark.asyncio
async def test_requested_model_changes_profile_metadata_for_token_budget():
    store = InMemorySessionStore()
    _seed_history(store, "session-requested-model")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(
        store,
        provider,
        max_context_tokens=120,
        context_reserve_tokens=0,
        compaction_preserve_recent_tokens=20,
        compaction_tail_turns=1,
    )

    result = await runner.run(
        session_id="session-requested-model",
        user_text="latest request",
        metadata={"requested_model": "github-copilot/gpt-5"},
    )

    assert result.status == LoopStatus.COMPLETED
    request_metadata = provider.requests[0].provider_request.metadata
    assert request_metadata["provider_id"] == "github-copilot"
    assert request_metadata["model_id"] == "gpt-5"
    assert request_metadata["max_context_chars"] == 480
    assert request_metadata["context_reserve_chars"] == 0
    assert request_metadata["compaction_preserve_recent_chars"] == 80


@pytest.mark.asyncio
async def test_max_context_chars_takes_priority_over_token_budget():
    store = InMemorySessionStore()
    _seed_history(store, "session-char-priority")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(
        store,
        provider,
        max_context_chars=60,
        max_context_tokens=10,
        context_reserve_tokens=5,
        compaction_preserve_recent_tokens=20,
        compaction_tail_turns=1,
    )

    result = await runner.run(
        session_id="session-char-priority",
        user_text="latest request",
    )

    assert result.status == LoopStatus.COMPLETED
    request_metadata = provider.requests[0].provider_request.metadata
    assert request_metadata["max_context_tokens"] == 10
    assert request_metadata["max_context_chars"] == 60
    assert request_metadata["context_reserve_tokens"] == 5
    assert request_metadata["context_reserve_chars"] == 20
    assert request_metadata["context_budget"]["max_context_chars_source"] == (
        "max_context_chars"
    )
    stored_compactions = _compaction_parts(store.read_history("session-char-priority"))
    assert stored_compactions[0].compaction.metadata["max_chars"] == 60
    assert stored_compactions[0].compaction.metadata["max_context_chars"] == 60


@pytest.mark.asyncio
async def test_auto_compaction_tail_zero_synthetic_replays_active_user():
    store = InMemorySessionStore()
    _seed_history(store, "session-replay")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(
        store,
        provider,
        max_context_parts=1,
        compaction_tail_turns=0,
    )

    result = await runner.run(
        session_id="session-replay",
        user_text="latest request must survive",
    )

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert request.provider_request.messages[-1].role == "user"
    assert request.provider_request.messages[-1].text == "latest request must survive"
    assert request.provider_request.metadata["compaction_replay"] is True
    replayed_message_id = request.provider_request.metadata["replayed_message_id"]

    stored = store.read_history("session-replay")
    replay_messages = _replay_messages(stored)
    assert len(replay_messages) == 1
    replay = replay_messages[0]
    assert replay.metadata["compaction_replay"] is True
    assert replay.metadata["compaction_trigger"] == "context_budget"
    assert replay.metadata["replayed_message_id"] == replayed_message_id
    assert replay.metadata["replay_message_id"] == replay.message_id
    assert replay.parts[0].type is MessagePartType.TEXT
    assert replay.parts[0].text == "latest request must survive"
    assert replay.parts[0].metadata["compaction_replay"] is True
    assert replay.parts[0].metadata["replayed_message_id"] == replayed_message_id
    assert _events(result, "session_compacted")[0].payload["compaction_replay"] is True
    assert (
        _events(result, "session_compacted")[0].payload["replayed_message_id"]
        == replayed_message_id
    )


@pytest.mark.asyncio
async def test_compaction_auto_false_leaves_stored_session_unchanged():
    store = InMemorySessionStore()
    _seed_history(store, "session-disabled")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(
        store,
        provider,
        max_context_parts=3,
        compaction_auto=False,
    )

    result = await runner.run(
        session_id="session-disabled",
        user_text="latest request",
    )

    assert result.status == LoopStatus.COMPLETED
    stored = store.read_history("session-disabled")
    assert [message.message_id for message in stored[:4]] == [
        "msg-old-0",
        "msg-old-1",
        "msg-old-2",
        "msg-old-3",
    ]
    assert stored[4].parts[0].text == "latest request"
    assert _compaction_parts(stored) == []
    assert _events(result, "session_compaction_started") == []


@pytest.mark.asyncio
async def test_provider_only_context_messages_are_not_persisted():
    store = InMemorySessionStore()
    _seed_history(store, "session-provider-only")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(
        store,
        provider,
        max_context_parts=3,
        compaction_tail_turns=1,
    )
    provider_only = Message.from_text(
        "system",
        "provider-only instruction",
        message_id="msg-provider-only",
    )

    result = await runner.run(
        session_id="session-provider-only",
        user_text="latest request",
        context_messages=[provider_only],
    )

    assert result.status == LoopStatus.COMPLETED
    assert provider.requests[0].provider_request.messages[0].text == (
        "provider-only instruction"
    )
    stored = store.read_history("session-provider-only")
    assert all(message.message_id != "msg-provider-only" for message in stored)
    assert all(
        part.text != "provider-only instruction"
        for message in stored
        for part in message.parts
    )


@pytest.mark.asyncio
async def test_overflow_retry_persists_overflow_compaction_and_retries_once():
    store = InMemorySessionStore()
    _seed_history(store, "session-overflow")
    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            {"content": "Recovered."},
        ]
    )
    runner = _runner(
        store,
        provider,
        max_context_parts=10,
        compaction_tail_turns=1,
    )

    result = await runner.run(
        session_id="session-overflow",
        user_text="latest request",
    )

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    retry_metadata = provider.metadata_snapshots[1]
    assert retry_metadata["overflow_retry"] is True
    assert retry_metadata["compaction"]["overflow"] is True
    assert retry_metadata["compaction"]["trigger"] == "provider_context_overflow"
    assert retry_metadata["compaction"]["overflow_retry"] is True
    assert provider.requests[1].provider_request.messages[-1].text == "latest request"

    stored_compactions = _compaction_parts(store.read_history("session-overflow"))
    assert len(stored_compactions) == 1
    compaction = stored_compactions[0].compaction
    assert compaction is not None
    assert compaction.overflow is True
    assert compaction.metadata["overflow_retry"] is True
    assert compaction.metadata["trigger"] == "provider_context_overflow"
    assert len(_events(result, "provider.context_overflow_retry")) == 1
    assert len(_events(result, "session_compaction_started")) == 1
    assert len(_events(result, "session_compacted")) == 1


@pytest.mark.asyncio
async def test_overflow_retry_request_contains_replayed_active_user():
    store = InMemorySessionStore()
    _seed_history(store, "session-overflow-replay", latest="retry latest request")
    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            {"content": "Recovered."},
        ]
    )
    runner = _runner(
        store,
        provider,
        max_context_parts=1,
        compaction_tail_turns=0,
    )

    result = await runner.run(
        session_id="session-overflow-replay",
        user_text="",
        append_user_message=False,
    )

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    retry_request = provider.requests[1]
    assert retry_request.provider_request.messages[-1].role == "user"
    assert retry_request.provider_request.messages[-1].text == "retry latest request"
    retry_metadata = provider.metadata_snapshots[1]
    assert retry_metadata["compaction_replay"] is True
    assert retry_metadata["replayed_message_id"] == "msg-latest-user"
    assert retry_metadata["overflow_retry"] is True
    assert retry_metadata["compaction"]["compaction_replay"] is True
    assert retry_metadata["compaction"]["replayed_message_id"] == "msg-latest-user"

    overflow_events = _events(result, "provider.context_overflow_retry")
    assert overflow_events[0].payload["compaction_replay"] is True
    assert overflow_events[0].payload["replayed_message_id"] == "msg-latest-user"
    assert overflow_events[0].payload["compaction"]["compaction_replay"] is True


@pytest.mark.asyncio
async def test_compaction_replay_replaces_attachment_with_placeholder_text():
    store = InMemorySessionStore()
    _seed_history(store, "session-attachment-replay")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(
        store,
        provider,
        max_context_parts=1,
        compaction_tail_turns=0,
    )
    attachment = Attachment(
        mime_type="text/plain",
        filename="notes.txt",
        text_ref="blob:notes",
    )

    result = await runner.run(
        session_id="session-attachment-replay",
        user_text="",
        user_parts=[MessagePart.attachment_part(attachment)],
    )

    assert result.status == LoopStatus.COMPLETED
    replay_text = "[Attachment omitted during compaction replay: notes.txt]"
    request_message = provider.requests[0].provider_request.messages[-1]
    assert request_message.role == "user"
    assert request_message.text == replay_text
    assert request_message.attachments == []

    stored = store.read_history("session-attachment-replay")
    replay_messages = _replay_messages(stored)
    assert len(replay_messages) == 1
    replay = replay_messages[0]
    assert replay.parts[0].type is MessagePartType.TEXT
    assert replay.parts[0].text == replay_text
    assert replay.parts[0].attachment is None
    assert replay.parts[0].metadata["compaction_replay"] is True
    assert all(
        part.type is not MessagePartType.ATTACHMENT
        for message in stored
        for part in message.parts
    )


@pytest.mark.asyncio
async def test_resume_path_uses_auto_session_compaction():
    store = InMemorySessionStore()
    _seed_history(store, "session-resume", latest="resume latest request")
    provider = ScriptedLLMProvider([{"content": "Resumed."}])
    runtime = AgentRuntime(
        provider=provider,
        store=store,
        config=RuntimeConfig(
            max_iterations=1,
            max_context_parts=3,
            compaction_tail_turns=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    result = await runtime.resume("session-resume", metadata={"run_id": "run-resume"})

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert _compaction_parts(request.messages)
    assert request.provider_request.messages[-1].role == "user"
    assert request.provider_request.messages[-1].text == "resume latest request"
    stored_compactions = _compaction_parts(store.read_history("session-resume"))
    assert len(stored_compactions) == 1
    assert stored_compactions[0].compaction.metadata["trigger"] == "context_budget"
