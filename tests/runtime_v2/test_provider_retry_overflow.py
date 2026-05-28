from __future__ import annotations

from copy import deepcopy
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
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, RuntimeRequest
from efp_runtime.models import Message, MessagePart, ToolCall
from efp_runtime.session.models import Session
from efp_runtime.session.store import InMemorySessionStore
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


def test_provider_retry_source_stays_inside_runtime_v2_import_boundary():
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
