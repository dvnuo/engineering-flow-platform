"""Stopping a run inside the runtime.

A stop asked for while the model is still talking ends the stream and keeps
what had arrived; a stop between tool calls answers the rest as cancelled; a
task cancelled outright still leaves no tool call waiting for a result; the
shell tool kills its command on a hard cancel; and a transport stream closed
early stops reading the provider's response.
"""

from __future__ import annotations

import asyncio
import io
import sys

import pytest

import efp_runtime.llm.provider as provider_module
from efp_runtime.event_bus import RuntimeEventBus
from efp_runtime.llm.events import LLMEvent, LLMEventType
from efp_runtime.llm.provider import GitHubCopilotHTTPTransport
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime
from efp_runtime.session.models import MessagePartType, MessageRole
from efp_runtime.tools.builtin import shell as shell_module
from efp_runtime.tools.definition import ToolContext, ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


def _tool(tool_id: str, execute) -> ToolDef:
    return ToolDef(
        id=tool_id,
        description=f"Test tool {tool_id}",
        input_schema={"type": "object", "properties": {}},
        execute=execute,
    )


def _tool_call(call_id: str, name: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}


def _assistant_text(message) -> str:
    return "".join(
        part.text or ""
        for part in message.parts
        if part.type is MessagePartType.TEXT
    )


class _StreamingProvider:
    """Streams a reply word by word and asks for a stop before the third word."""

    def __init__(self) -> None:
        self.runtime: AgentRuntime | None = None
        self.session_id = ""
        self.closed = False
        self.yielded = 0

    async def invoke(self, request):
        return self._stream()

    async def _stream(self):
        try:
            yield LLMEvent(LLMEventType.STEP_START)
            yield LLMEvent(LLMEventType.MESSAGE_START)
            for word in ("Hello", " there", " and", " more", " words"):
                self.yielded += 1
                if self.yielded == 3 and self.runtime is not None:
                    self.runtime.cancel(self.session_id)
                yield LLMEvent(LLMEventType.TEXT_DELTA, delta=word, part_id="text_0")
            yield LLMEvent(LLMEventType.TEXT_END, part_id="text_0")
            yield LLMEvent(
                LLMEventType.STEP_FINISH,
                usage={"input_tokens": 3, "output_tokens": 5},
            )
        finally:
            self.closed = True


@pytest.mark.asyncio
async def test_cancel_while_streaming_keeps_the_partial_reply_and_closes_the_stream():
    bus = RuntimeEventBus()
    provider = _StreamingProvider()
    runtime = AgentRuntime(
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        event_bus=bus,
    )
    session_id = "session-stream-cancel"
    provider.runtime = runtime
    provider.session_id = session_id

    result = await asyncio.wait_for(
        runtime.run("Say many words.", session_id=session_id),
        timeout=5,
    )

    assert result.status == LoopStatus.CANCELLED
    # The provider's stream was closed, not drained to the end.
    assert provider.closed is True
    assert provider.yielded == 3

    history = runtime.store.read_history(session_id)
    assistant = [message for message in history if message.role is MessageRole.ASSISTANT][-1]
    assert _assistant_text(assistant) == "Hello there"
    assert assistant.status == "cancelled"
    assert assistant.metadata["cancelled"] is True
    assert result.final_assistant_message is not None
    assert _assistant_text(result.final_assistant_message) == "Hello there"

    events = bus.history(session_id)
    cancelled = [event for event in events if event.type == "run_cancelled"]
    assert cancelled and cancelled[0].payload["phase"] == "provider_stream"
    assert events[-1].type == "run_finish"
    assert events[-1].payload["status"] == LoopStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_between_tool_calls_answers_the_rest_and_the_next_turn_starts_fresh():
    bus = RuntimeEventBus()
    second_tool_runs: list[dict] = []

    async def request_cancel(args, context):
        runtime.cancel(context.session_id)
        return "cancel requested"

    async def second_tool(args, context):
        second_tool_runs.append(dict(args))
        return "ran"

    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    _tool_call("call_cancel", "request_cancel"),
                    _tool_call("call_second", "second_tool"),
                ]
            },
            {"content": "Fresh start."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    _tool("request_cancel", request_cancel),
                    _tool("second_tool", second_tool),
                ]
            )
        ),
        max_iterations=3,
        event_bus=bus,
    )
    session_id = "session-cancel-settles-tool-calls"

    result = await runtime.run("Please cancel.", session_id=session_id)

    assert result.status == LoopStatus.CANCELLED
    assert second_tool_runs == []
    pairs = runtime.store.tool_pairs(session_id)
    # Every tool call has an answer: the one that ran, and the one the stop
    # pre-empted, which says it was cancelled.
    assert all(pair[1] is not None for pair in pairs.values())
    settled = pairs["call_second"][1].tool_result
    assert settled is not None
    assert settled.status == "cancelled"
    assert settled.success is False
    assert "stopped the run" in settled.content
    assert pairs["call_second"][0].tool_call.status == "cancelled"

    # The next message is answered from the member's text; the stopped tool
    # call is not resumed first.
    second = await runtime.run("Next message.", session_id=session_id)

    assert second.status == LoopStatus.COMPLETED
    assert second_tool_runs == []
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_forced_cancel_during_a_tool_leaves_no_dangling_tool_call():
    started = asyncio.Event()

    async def slow_tool(args, context):
        started.set()
        await asyncio.sleep(30)
        return "never"

    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call_slow", "slow_tool")]},
            {"content": "This should not be requested."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry([_tool("slow_tool", slow_tool)])),
        max_iterations=3,
    )
    session_id = "session-forced-cancel"

    task = asyncio.create_task(runtime.run("Run the slow tool.", session_id=session_id))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    pairs = runtime.store.tool_pairs(session_id)
    assert pairs["call_slow"][1] is not None
    assert pairs["call_slow"][1].tool_result.status == "cancelled"
    run_record = runtime.run_state.current(session_id)
    assert run_record is not None
    assert run_record.active is False
    assert run_record.status == LoopStatus.CANCELLED
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_shell_hard_cancel_kills_the_running_command():
    # The process directly, not through a shell: on Windows the shell is what
    # gets killed and the command it started keeps the pipes open, which is a
    # property of the platform, not of the code under test.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    context = ToolContext(cancel_requested=lambda: False)
    task = asyncio.create_task(
        shell_module._communicate_with_timeout_and_cancel(
            process,
            timeout=30,
            context=context,
        )
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Killed, not left to sleep out its 30 seconds with nobody reading it.
    await asyncio.wait_for(process.wait(), timeout=5)
    assert process.returncode is not None


class _ClosableStreamingResponse:
    def __init__(self, body: bytes) -> None:
        self._lines = io.BytesIO(body)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def readline(self):
        return self._lines.readline()

    def close(self):
        self.closed = True
        self._lines.close()


@pytest.mark.asyncio
async def test_copilot_stream_closed_early_stops_reading_the_response(monkeypatch):
    body = b"".join(
        b'data: {"type":"response.output_text.delta","delta":"w%d"}\n\n' % index
        for index in range(500)
    )
    response = _ClosableStreamingResponse(body + b"data: [DONE]\n\n")
    monkeypatch.setattr(provider_module.urllib_request, "urlopen", lambda request, timeout: response)

    transport = GitHubCopilotHTTPTransport(token="secret-token", timeout=12)
    stream = await transport.send({"model": "gpt-5-mini", "stream": True})
    first = await stream.__anext__()
    assert first["delta"] == "w0"

    await stream.aclose()

    assert response.closed is True


@pytest.mark.asyncio
async def test_closing_event_stream_closes_the_transport_stream_too():
    closed: list[str] = []

    async def source():
        try:
            yield {"type": "chunk", "n": 1}
            yield {"type": "chunk", "n": 2}
        finally:
            closed.append("source")

    async def events(chunks):
        try:
            async for chunk in chunks:
                yield chunk
        finally:
            closed.append("events")

    chunks = source()
    stream = provider_module._ClosingEventStream(events(chunks), chunks)
    assert (await stream.__anext__())["n"] == 1

    await stream.aclose()

    assert closed == ["events", "source"]
