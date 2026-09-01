"""A transient upstream failure should be retried, not surfaced.

Reported symptom: Copilot returned HTTP 500, the run died with the raw transport
message, and simply telling the agent to continue worked. That is the signature
of a retryable failure reaching the user.

Root cause: the loop has a full retry path -- backoff, retry events, an attempt
cap -- gated on ProviderTransientError, but nothing ever raised one. Every
transport failure came out as a bare ProviderTransportError (a plain
RuntimeError with no retry metadata) and propagated straight through.
"""
import urllib.error as urllib_error

import pytest

from efp_runtime.llm.errors import ProviderTransientError
from efp_runtime.llm.provider import (
    RETRYABLE_TRANSPORT_STATUS_CODES,
    ProviderTransportError,
    ProviderTransportTransientError,
    _transport_error_for_http_status,
)


# ------------------------------------------------------------ classification


@pytest.mark.parametrize("status", sorted(RETRYABLE_TRANSPORT_STATUS_CODES))
def test_upstream_failures_are_retryable(status):
    error = _transport_error_for_http_status("boom", status)

    assert isinstance(error, ProviderTransportTransientError)
    assert error.retryable is True
    assert error.status_code == status


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, None])
def test_client_errors_are_not_retryable(status):
    # These fail again identically, so retrying only delays the report.
    error = _transport_error_for_http_status("boom", status)

    assert not isinstance(error, ProviderTransportTransientError)
    assert type(error) is ProviderTransportError


def test_a_transient_transport_error_satisfies_both_handlers():
    # The loop retries on ProviderTransientError; the gateway maps
    # ProviderTransportError to an HTTP status once retries are exhausted.
    # Losing either catch would change behaviour somewhere.
    error = ProviderTransportTransientError("boom", status_code=503)

    assert isinstance(error, ProviderTransientError)
    assert isinstance(error, ProviderTransportError)
    assert error.metadata["status_code"] == 503
    assert error.code == "transport_transient"


# --------------------------------------------------- the reported scenario


class _FlakyHTTPTransport:
    """Raises the reported 500 once, then succeeds."""

    def __init__(self, failures: int = 1, status: int = 500):
        self.failures = failures
        self.status = status
        self.attempts = 0

    def send(self, payload):
        self.attempts += 1
        if self.attempts <= self.failures:
            exc = urllib_error.HTTPError(
                url="https://api.githubcopilot.com/responses",
                code=self.status,
                msg="Internal Server Error",
                hdrs=None,
                fp=None,
            )
            raise _transport_error_for_http_status(
                f"GitHub Copilot HTTP transport failed with status {self.status} "
                f"(Internal Server Error) response: Internal Server Error",
                exc.code,
            ) from None
        return {"ok": True}


def test_the_reported_500_is_classified_as_retryable():
    transport = _FlakyHTTPTransport()

    with pytest.raises(ProviderTransientError) as excinfo:
        transport.send({})

    assert excinfo.value.retryable is True
    assert "status 500" in str(excinfo.value)


def test_a_persistent_500_still_surfaces_after_the_attempt_cap():
    # Retrying must not hide a provider that is genuinely down.
    transport = _FlakyHTTPTransport(failures=99)

    for _ in range(3):
        with pytest.raises(ProviderTransientError):
            transport.send({})

    assert transport.attempts == 3


# -------------------------------------------------- end-to-end in the loop


@pytest.mark.asyncio
async def test_the_loop_retries_a_transient_transport_failure():
    from tests.runtime.test_provider_retry_overflow import (
        StreamSequenceProvider,
        _events,
        _runner,
    )
    from efp_runtime.llm.events import LLMEvent, LLMEventType
    from efp_runtime.loop import LoopStatus

    provider = StreamSequenceProvider(
        [
            ProviderTransportTransientError(
                "GitHub Copilot HTTP transport failed with status 500 "
                "(Internal Server Error) response: Internal Server Error",
                status_code=500,
            ),
            [
                LLMEvent(LLMEventType.STEP_START),
                LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_0", delta="Recovered."),
                LLMEvent(LLMEventType.STEP_FINISH),
            ],
        ]
    )
    runner = _runner(provider, provider_retry_backoff_seconds=0)

    result = await runner.run(session_id="session-transport-500", user_text="hi")

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert len(_events(result, "provider.retry")) == 1
    assert result.final_assistant_message.parts[0].text == "Recovered."


@pytest.mark.asyncio
async def test_a_non_retryable_transport_failure_still_stops_the_run():
    from tests.runtime.test_provider_retry_overflow import StreamSequenceProvider, _runner

    from efp_runtime.loop import LoopStatus

    provider = StreamSequenceProvider([ProviderTransportError("Bad request")])
    runner = _runner(provider, provider_retry_backoff_seconds=0)

    result = await runner.run(session_id="session-transport-400", user_text="hi")

    # Reported as an error result rather than retried: one attempt, and no
    # provider.retry event to suggest otherwise.
    assert result.status == LoopStatus.ERROR
    assert len(provider.requests) == 1
    assert not [event for event in result.runtime_events if event.type == "provider.retry"]
