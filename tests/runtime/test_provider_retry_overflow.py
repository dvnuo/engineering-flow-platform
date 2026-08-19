from __future__ import annotations

from copy import deepcopy
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.llm.errors import (
    ProviderContextOverflowError,
    ProviderError,
    ProviderFatalError,
    ProviderTransientError,
)
from efp_runtime.llm.events import LLMEvent, LLMEventType
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, RuntimeRequest
from efp_runtime.loop.runner import _prefetch_async_stream
from efp_runtime.models import Message, MessagePart, ToolCall
from efp_runtime.session.models import Session
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


class SequenceProvider:
    def __init__(self, steps):
        self.steps = list(steps)
        self.requests: list[RuntimeRequest] = []
        self.metadata_snapshots: list[dict] = []
        self.message_snapshots: list[list[tuple[str, str]]] = []

    async def invoke(self, request: RuntimeRequest):
        self.requests.append(request)
        self.metadata_snapshots.append(deepcopy(request.provider_request.metadata))
        self.message_snapshots.append(
            [
                (message.role, message.text)
                for message in request.provider_request.messages
            ]
        )
        if not self.steps:
            raise AssertionError("SequenceProvider has no step left")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class StreamSequenceProvider:
    """Provider whose invoke() returns an *unstarted* async generator per call.

    This is the shape every streaming provider has (OpenAICompatibleProvider
    returns ``adapter.normalize_stream(...)``), so a failure raised before the
    first yield only reaches the runner if the runner starts the stream itself.
    """

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests: list[RuntimeRequest] = []
        self.message_snapshots: list[list[tuple[str, str]]] = []

    def invoke(self, request: RuntimeRequest):
        self.requests.append(request)
        self.message_snapshots.append(
            [
                (message.role, message.text)
                for message in request.provider_request.messages
            ]
        )
        if not self.steps:
            raise AssertionError("StreamSequenceProvider has no step left")
        return self._stream(self.steps.pop(0))

    async def _stream(self, step):
        if isinstance(step, BaseException):
            raise step
        for event in step:
            yield event


def _runner(provider, **kwargs) -> RuntimeLoopRunner:
    return RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        **kwargs,
    )


def _old_session(*texts: str) -> Session:
    return Session(
        session_id="session-provider-retry",
        messages=[
            Message.from_text("user", text, message_id=f"msg-{index}")
            for index, text in enumerate(texts)
        ],
    )


def _events(result, event_type: str):
    return [event for event in result.runtime_events if event.type == event_type]


def _stored_fingerprint(message) -> tuple:
    return (
        message.message_id,
        message.role,
        tuple(part.text for part in message.parts),
    )


@pytest.mark.asyncio
async def test_transient_provider_error_retries_and_then_succeeds():
    provider = SequenceProvider(
        [
            ProviderTransientError("temporary outage", code="rate_limit"),
            {"content": "Recovered."},
        ]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-transient", user_text="hello")

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    retry_events = _events(result, "provider.retry")
    assert len(retry_events) == 1
    assert retry_events[0].payload["attempt"] == 1
    assert retry_events[0].payload["max_retries"] == 2
    assert retry_events[0].payload["error_type"] == "ProviderTransientError"
    assert retry_events[0].payload["code"] == "rate_limit"

    retry_metadata = provider.metadata_snapshots[1]["provider_retry"]
    assert retry_metadata["retry_count"] == 1
    assert retry_metadata["last_error"]["code"] == "rate_limit"


@pytest.mark.asyncio
async def test_provider_max_retries_zero_does_not_retry():
    provider = SequenceProvider(
        [ProviderTransientError("temporary outage", code="temporary")]
    )
    runner = _runner(provider, provider_max_retries=0)

    result = await runner.run(session_id="session-no-retry", user_text="hello")

    assert result.status == LoopStatus.ERROR
    assert len(provider.requests) == 1
    assert _events(result, "provider.retry") == []
    error_events = _events(result, "error")
    assert error_events[-1].payload["code"] == "temporary"


@pytest.mark.asyncio
async def test_retryable_provider_error_retries_without_sdk_specific_type():
    provider = SequenceProvider(
        [
            ProviderError(
                "retryable provider boundary",
                retryable=True,
                code="provider_retryable",
            ),
            {"content": "Recovered."},
        ]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-retryable", user_text="hello")

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    retry_events = _events(result, "provider.retry")
    assert len(retry_events) == 1
    assert retry_events[0].payload["error_type"] == "ProviderError"
    assert retry_events[0].payload["code"] == "provider_retryable"


@pytest.mark.asyncio
async def test_fatal_provider_error_does_not_retry():
    provider = SequenceProvider(
        [ProviderFatalError("bad request", code="invalid_request")]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-fatal", user_text="hello")

    assert result.status == LoopStatus.ERROR
    assert len(provider.requests) == 1
    assert _events(result, "provider.retry") == []
    error_events = _events(result, "error")
    assert error_events[-1].payload["retryable"] is False
    assert error_events[-1].payload["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_non_retryable_provider_error_does_not_retry():
    provider = SequenceProvider(
        [ProviderError("quota disabled", retryable=False, code="quota_disabled")]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-non-retryable", user_text="hello")

    assert result.status == LoopStatus.ERROR
    assert len(provider.requests) == 1
    assert _events(result, "provider.retry") == []
    assert _events(result, "error")[-1].payload["code"] == "quota_disabled"


@pytest.mark.asyncio
async def test_transient_provider_error_exhausts_max_retries():
    provider = SequenceProvider(
        [
            ProviderTransientError("temporary-1", code="unavailable"),
            ProviderTransientError("temporary-2", code="unavailable"),
            ProviderTransientError("temporary-3", code="unavailable"),
        ]
    )
    runner = _runner(provider, provider_max_retries=2)

    result = await runner.run(session_id="session-exhaust", user_text="hello")

    assert result.status == LoopStatus.ERROR
    assert len(provider.requests) == 3
    retry_events = _events(result, "provider.retry")
    assert [event.payload["attempt"] for event in retry_events] == [1, 2]
    assert retry_events[-1].payload["code"] == "unavailable"

    retry_metadata = provider.requests[-1].metadata["provider_retry"]
    assert retry_metadata["retry_count"] == 2
    assert retry_metadata["max_retries"] == 2
    assert retry_metadata["last_error"]["message"] == "temporary-3"
    error_events = _events(result, "error")
    assert error_events[-1].payload["error"] == "temporary-3"
    assert error_events[-1].payload["code"] == "unavailable"


@pytest.mark.asyncio
async def test_context_overflow_triggers_compacted_retry_and_succeeds():
    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            {"content": "Recovered after compaction."},
        ]
    )
    runner = _runner(provider, max_context_parts=5)

    result = await runner.run(
        session=_old_session("old 1", "old 2", "old 3", "old 4"),
        user_text="latest request",
    )

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert "compaction" not in provider.metadata_snapshots[0]

    retry_metadata = provider.metadata_snapshots[1]
    assert retry_metadata["overflow_retry"] is True
    assert retry_metadata["compaction"]["overflow_retry"] is True
    assert retry_metadata["compaction"]["max_parts"] == 2
    assert provider.requests[1].prepared_request.compaction_applied is True
    assert provider.requests[1].prepared_request.compaction_metadata[
        "overflow_retry"
    ] is True

    overflow_events = _events(result, "provider.context_overflow_retry")
    assert len(overflow_events) == 1
    assert overflow_events[0].payload["attempt"] == 1
    assert overflow_events[0].payload["compaction"]["overflow_retry"] is True
    assert provider.message_snapshots[1][-1] == ("user", "latest request")


@pytest.mark.asyncio
async def test_context_overflow_retry_preserves_max_steps_text_only_request():
    async def execute(args, context):
        return "unused"

    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            {"content": "Stopped after max steps."},
        ]
    )
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="again",
                        description="Return unused",
                        input_schema={"type": "object", "properties": {}},
                        execute=execute,
                    )
                ]
            )
        ),
        max_iterations=1,
        max_context_parts=5,
    )

    result = await runner.run(
        session=_old_session("old 1", "old 2", "old 3", "old 4"),
        user_text="latest request",
    )

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert provider.requests[0].provider_request.tools == []
    assert provider.requests[1].provider_request.tools == []
    assert provider.metadata_snapshots[1]["max_steps_reached"] is True
    assert provider.metadata_snapshots[1]["overflow_retry"] is True
    assert provider.message_snapshots[1][-1][0] == "assistant"
    assert "CRITICAL - MAXIMUM STEPS REACHED" in provider.message_snapshots[1][-1][1]
    assert any(event.type == "loop.max_iterations" for event in result.runtime_events)


@pytest.mark.asyncio
async def test_context_overflow_only_retries_once():
    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            ProviderContextOverflowError("still too long"),
        ]
    )
    runner = _runner(provider, max_context_parts=5)

    result = await runner.run(
        session=_old_session("old 1", "old 2", "old 3", "old 4"),
        user_text="latest request",
    )

    assert result.status == LoopStatus.ERROR
    assert len(provider.requests) == 2
    assert len(_events(result, "provider.context_overflow_retry")) == 1
    assert _events(result, "error")[-1].payload["code"] == "context_overflow"


@pytest.mark.asyncio
async def test_context_overflow_retry_preserves_latest_user_request():
    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            {"content": "Done."},
        ]
    )
    runner = _runner(provider, max_context_chars=120)

    result = await runner.run(
        session=_old_session("old " * 80, "older " * 80),
        user_text="latest user request must stay",
    )

    assert result.status == LoopStatus.COMPLETED
    assert provider.message_snapshots[1][-1] == (
        "user",
        "latest user request must stay",
    )


@pytest.mark.asyncio
async def test_context_overflow_retry_preserves_pending_tool_call():
    pending_call = ToolCall(
        call_id="call-pending",
        tool_name="write_file",
        arguments={"path": "out.txt"},
    )
    context_messages = [
        Message.from_text("user", "old " * 80, message_id="msg-old-context"),
        Message(
            role="assistant",
            message_id="msg-pending",
            parts=[MessagePart.tool_call_part(pending_call)],
        ),
    ]
    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            {"content": "Done."},
        ]
    )
    runner = _runner(provider, max_context_parts=2)

    result = await runner.run(
        session_id="session-pending-context",
        user_text="latest request",
        context_messages=context_messages,
    )

    assert result.status == LoopStatus.COMPLETED
    calls = [
        call.call_id
        for message in provider.requests[1].provider_request.messages
        for call in message.tool_calls
    ]
    assert calls == ["call-pending"]
    assert provider.message_snapshots[1][-1] == ("user", "latest request")


@pytest.mark.asyncio
async def test_stream_overflow_before_first_chunk_triggers_compacted_retry():
    provider = StreamSequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            [
                LLMEvent(LLMEventType.STEP_START),
                LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_0", delta="Recovered."),
                LLMEvent(LLMEventType.STEP_FINISH),
            ],
        ]
    )
    runner = _runner(provider, max_context_parts=5)

    result = await runner.run(
        session=_old_session("old 1", "old 2", "old 3", "old 4"),
        user_text="latest request",
    )

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert len(_events(result, "provider.context_overflow_retry")) == 1
    assert provider.requests[1].metadata["overflow_retry"] is True
    assert provider.message_snapshots[1][-1] == ("user", "latest request")
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == "Recovered."


@pytest.mark.asyncio
async def test_stream_overflow_retry_emits_llm_events_only_for_the_served_attempt():
    provider = StreamSequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            [
                LLMEvent(LLMEventType.STEP_START),
                LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_0", delta="Recovered."),
                LLMEvent(LLMEventType.STEP_FINISH),
            ],
        ]
    )
    runner = _runner(provider, max_context_parts=5)

    result = await runner.run(
        session=_old_session("old 1", "old 2", "old 3", "old 4"),
        user_text="latest request",
    )

    # The discarded first attempt must not leak llm.* events; prefetching the
    # first chunk happens before the stream-event bridge runs.
    assert [event.payload["delta"] for event in _events(result, "llm.text_delta")] == [
        "Recovered."
    ]
    assert len(_events(result, "llm.step_finish")) == 1


@pytest.mark.asyncio
async def test_prefetch_replays_the_consumed_head_chunk():
    # _prefetch_async_stream pulls the first chunk off the provider stream to
    # surface a pre-first-yield failure. That chunk is already consumed, so the
    # replay wrapper has to hand it back or every stream silently loses its head.
    async def three_chunks():
        yield "head"
        yield "middle"
        yield "tail"

    replayed = await _prefetch_async_stream(three_chunks())
    assert [chunk async for chunk in replayed] == ["head", "middle", "tail"]

    async def no_chunks():
        return
        yield  # pragma: no cover - marks this an async generator

    empty = await _prefetch_async_stream(no_chunks())
    assert [chunk async for chunk in empty] == []


@pytest.mark.asyncio
async def test_closing_the_replay_wrapper_closes_the_provider_stream():
    # `async for` does not close the iterator it drives, and closing an async
    # generator does not cascade into the generator it was iterating. Without an
    # explicit forward, abandoning the replay wrapper mid-stream leaves the
    # provider stream suspended at its `yield`, so the transport's `finally`
    # (which cancels the worker task feeding the queue) never runs until GC.
    closed = False

    async def source():
        nonlocal closed
        try:
            yield "head"
            yield "middle"
            yield "tail"
        finally:
            closed = True

    stream = source()  # held explicitly so refcount finalization cannot mask it
    replayed = await _prefetch_async_stream(stream)

    seen = []
    async for chunk in replayed:
        seen.append(chunk)
        if len(seen) == 2:
            break  # abandon mid-stream

    assert seen == ["head", "middle"]
    assert inspect.getasyncgenstate(stream) == "AGEN_SUSPENDED"

    await replayed.aclose()

    assert closed, "closing the replay wrapper must close the provider stream"
    assert inspect.getasyncgenstate(stream) == "AGEN_CLOSED"


@pytest.mark.asyncio
async def test_replay_wrapper_tolerates_a_provider_iterator_without_aclose():
    # A provider may hand back a plain class-based async iterator (`__aiter__`
    # returning self) which has no `aclose`. Forwarding closure must not turn
    # that into an AttributeError raised out of the wrapper's cleanup.
    class PlainAsyncIterator:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._chunks:
                raise StopAsyncIteration
            return self._chunks.pop(0)

    replayed = await _prefetch_async_stream(PlainAsyncIterator(["head", "tail"]))
    assert [chunk async for chunk in replayed] == ["head", "tail"]

    replayed = await _prefetch_async_stream(PlainAsyncIterator(["head", "tail"]))
    assert await replayed.__anext__() == "head"
    await replayed.aclose()  # must not raise AttributeError


@pytest.mark.asyncio
async def test_stream_first_text_delta_survives_the_prefetch():
    # End-to-end guard for the same head chunk: the first *normalized* event of
    # a stream is often already a TEXT_DELTA, and dropping it would silently
    # truncate the assistant's answer rather than fail loudly.
    provider = StreamSequenceProvider(
        [
            [
                LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_0", delta="HEAD-"),
                LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_0", delta="TAIL"),
                LLMEvent(LLMEventType.STEP_FINISH),
            ]
        ]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-stream-head", user_text="hi")

    assert result.status == LoopStatus.COMPLETED
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == "HEAD-TAIL"


@pytest.mark.asyncio
async def test_stream_transient_error_before_first_chunk_is_retried():
    # Scope note, pinned deliberately: prefetching the first chunk makes *every*
    # retryable provider error raised before the first yield reachable by the
    # retry loop, not only ProviderContextOverflowError. Nothing was yielded, so
    # no partial assistant message and no tool call can have escaped.
    provider = StreamSequenceProvider(
        [
            ProviderTransientError("stream connect failed"),
            [
                LLMEvent(LLMEventType.STEP_START),
                LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_0", delta="Recovered."),
                LLMEvent(LLMEventType.STEP_FINISH),
            ],
        ]
    )
    runner = _runner(provider, provider_retry_backoff_seconds=0)

    result = await runner.run(session_id="session-stream-transient", user_text="hi")

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert len(_events(result, "provider.retry")) == 1
    assert not _events(result, "provider.context_overflow_retry")
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == "Recovered."


_STOCK_BUDGET_TURN_TEXTS = tuple(
    # The catalog default for the stock model leaves ~1.05M prompt chars, halved
    # to ~528k on the overflow retry. The transcript has to exceed the halved
    # budget or "the retry sends less" would be vacuously false.
    "bulk turn {0} ".format(index) * 30_000
    for index in range(10)
)


@pytest.mark.asyncio
async def test_overflow_retry_shrinks_request_without_rewriting_stored_history():
    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            {"content": "Recovered without rewriting the transcript."},
        ]
    )
    store = InMemorySessionStore()
    # Stock runner: no max_context_parts/chars/tokens. The catalog-derived budget
    # sizes the in-memory request only, so the overflow retry must compact at
    # render time and leave the stored transcript alone.
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )
    seeded = _old_session(*_STOCK_BUDGET_TURN_TEXTS)
    seeded_messages = [_stored_fingerprint(message) for message in seeded.messages]

    result = await runner.run(session=seeded, user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert len(_events(result, "provider.context_overflow_retry")) == 1

    # The stored transcript is untouched: same ids, same text, still leading.
    stored = store.read_history("session-provider-retry")
    assert [
        _stored_fingerprint(message) for message in stored[: len(seeded_messages)]
    ] == seeded_messages
    assert _events(result, "session_compacted") == []
    assert _events(result, "session_compaction_started") == []

    # ...and the retry still sent strictly less than the first attempt.
    first_chars = sum(len(text) for _role, text in provider.message_snapshots[0])
    retry_chars = sum(len(text) for _role, text in provider.message_snapshots[1])
    assert retry_chars < first_chars
    assert provider.requests[1].prepared_request.compaction_applied is True


@pytest.mark.asyncio
async def test_overflow_retry_shrinks_request_with_a_size_knob_and_no_stored_rewrite():
    """The sibling of the stock-runner test, but with a size cap configured.

    A configured cap used to imply stored-history rewriting, which pre-shrank
    attempt #1. Now it does not, so attempt #1 is larger than it used to be -
    but the retry still lands on the same halved render-time budget and still
    recovers, with the transcript intact. That is the whole argument for
    decoupling: the size knob keeps doing its job without the disk rewrite.
    """

    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            {"content": "Recovered without rewriting the transcript."},
        ]
    )
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        max_context_parts=5,
    )
    seeded = _old_session(*_STOCK_BUDGET_TURN_TEXTS)
    seeded_messages = [_stored_fingerprint(message) for message in seeded.messages]

    result = await runner.run(session=seeded, user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert len(_events(result, "provider.context_overflow_retry")) == 1

    stored = store.read_history("session-provider-retry")
    assert [
        _stored_fingerprint(message) for message in stored[: len(seeded_messages)]
    ] == seeded_messages
    assert _events(result, "session_compacted") == []
    assert _events(result, "session_compaction_started") == []

    first_chars = sum(len(text) for _role, text in provider.message_snapshots[0])
    retry_chars = sum(len(text) for _role, text in provider.message_snapshots[1])
    assert retry_chars < first_chars
    assert provider.message_snapshots[1][-1] == ("user", "latest request")


def test_provider_retry_error_import_boundary():
    code = """
import json
import sys

from efp_runtime.llm.errors import ProviderTransientError
from efp_runtime.loop import ProviderContextOverflowError

print(json.dumps({
    "legacy_core_loaded": "src.agents.core" in sys.modules,
    "transient": ProviderTransientError("x").retryable,
    "overflow_code": ProviderContextOverflowError("x").code,
}))
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
    assert payload == {
        "legacy_core_loaded": False,
        "transient": True,
        "overflow_code": "context_overflow",
    }


def test_provider_retry_source_stays_inside_runtime_import_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime").rglob("*.py"))
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
