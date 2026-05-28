"""Provider transport facade for Runtime v2 OpenAI-compatible clients.

The classes in this module do not perform network I/O and do not import an
OpenAI SDK. A caller injects the transport boundary, which receives the
projected payload and returns raw provider data.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from copy import deepcopy
import inspect
from typing import TYPE_CHECKING, Any, List, Optional, Protocol, Union

from .adapter import DefaultLLMEventAdapter, LLMEventAdapter
from .openai import (
    provider_request_to_openai_chat,
    provider_request_to_openai_responses,
)

if TYPE_CHECKING:
    from ..loop.provider import ProviderOutput, RuntimeRequest


TransportOutput = Union[
    Mapping[str, Any],
    Iterable[Mapping[str, Any]],
    AsyncIterable[Mapping[str, Any]],
]


class ProviderTransport(Protocol):
    """Injectable boundary that sends a projected provider payload."""

    async def send(self, payload: dict[str, Any]) -> TransportOutput:
        ...


class ProviderTransportError(RuntimeError):
    """Raised by transports or helpers when provider transport fails."""


class OpenAICompatibleProvider:
    """LLMProvider implementation for OpenAI-compatible payload transports."""

    def __init__(
        self,
        *,
        model: str,
        transport: ProviderTransport,
        endpoint: str = "chat",
        instructions: Optional[str] = None,
        stream: bool = False,
        metadata: Optional[Mapping[str, Any]] = None,
        adapter: Optional[LLMEventAdapter] = None,
    ) -> None:
        if endpoint not in {"chat", "responses"}:
            raise ValueError("endpoint must be 'chat' or 'responses'")
        self.model = model
        self.transport = transport
        self.endpoint = endpoint
        self.instructions = instructions
        self.stream = stream
        self.metadata = dict(metadata or {})
        self.adapter = adapter or DefaultLLMEventAdapter()

    def build_payload(self, request: RuntimeRequest) -> dict[str, Any]:
        """Project a RuntimeRequest into the configured provider payload."""

        if self.endpoint == "responses":
            return provider_request_to_openai_responses(
                request.provider_request,
                model=self.model,
                instructions=self.instructions,
                stream=self.stream,
                metadata=self.metadata,
            )
        return provider_request_to_openai_chat(
            request.provider_request,
            model=self.model,
            instructions=self.instructions,
            stream=self.stream,
            metadata=self.metadata,
        )

    async def invoke(self, request: RuntimeRequest) -> ProviderOutput:
        payload = self.build_payload(request)
        try:
            raw_output = self.transport.send(payload)
            if inspect.isawaitable(raw_output):
                raw_output = await raw_output
        except Exception as exc:
            return self._transport_error_response(exc)

        if self.stream:
            if isinstance(raw_output, Mapping):
                return self.adapter.normalize_response(raw_output)
            return self.adapter.normalize_stream(raw_output)

        if not isinstance(raw_output, Mapping):
            return self._transport_error_response(
                ProviderTransportError("non-stream transport returned a stream response")
            )
        return raw_output

    def _transport_error_response(self, exc: BaseException) -> dict[str, Any]:
        message = str(exc) or exc.__class__.__name__
        return {
            "error": {
                "message": "OpenAI-compatible transport failed: {0}".format(message),
                "type": "transport_error",
                "exception": exc.__class__.__name__,
            },
            "metadata": {
                "provider": "openai",
                "endpoint": self.endpoint,
                "model": self.model,
            },
        }


class RecordingTransport:
    """Small deterministic transport for tests and local prototypes."""

    def __init__(self, responses: Iterable[Union[TransportOutput, BaseException]]) -> None:
        self._responses = list(responses)
        self.payloads: List[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> TransportOutput:
        self.payloads.append(deepcopy(payload))
        if not self._responses:
            raise AssertionError("RecordingTransport has no response left")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    @property
    def requests(self) -> List[dict[str, Any]]:
        return self.payloads

    @property
    def remaining(self) -> int:
        return len(self._responses)


__all__ = [
    "OpenAICompatibleProvider",
    "ProviderTransport",
    "ProviderTransportError",
    "RecordingTransport",
    "TransportOutput",
]
