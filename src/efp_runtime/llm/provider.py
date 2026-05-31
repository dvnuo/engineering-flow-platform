"""Provider transport facade for EFP runtime OpenAI-compatible clients.

The facade classes in this module do not import an OpenAI SDK. A caller injects
the transport boundary, which receives the projected payload and returns raw
provider data.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Iterable, Mapping
from copy import deepcopy
import inspect
import json
import os
from typing import TYPE_CHECKING, Any, List, Optional, Protocol, Union
from urllib import error as urllib_error
from urllib import request as urllib_request

from .adapter import DefaultLLMEventAdapter, LLMEventAdapter
from .models import DEFAULT_MODEL_ID, DEFAULT_PROVIDER_ID
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


class GitHubCopilotHTTPTransport:
    """Standard-library HTTP JSON transport for GitHub Copilot chat completions."""

    DEFAULT_BASE_URL = "https://api.githubcopilot.com"
    CHAT_COMPLETIONS_PATH = "/chat/completions"

    def __init__(
        self,
        *,
        token: str,
        base_url: Optional[str] = None,
        timeout: float = 60,
        user_agent: str = "efp-runtime",
        initiator: str = "user",
    ) -> None:
        self._token = _required_non_empty_string(token, "token")
        self.base_url = _normalize_base_url(base_url)
        self.timeout = timeout
        self.user_agent = _required_non_empty_string(user_agent, "user_agent")
        self.initiator = _required_non_empty_string(initiator, "initiator")
        self.endpoint = "{0}{1}".format(self.base_url, self.CHAT_COMPLETIONS_PATH)

    async def send(self, payload: dict[str, Any]) -> TransportOutput:
        if payload.get("stream") is True:
            raise ProviderTransportError(
                "GitHub Copilot HTTP transport does not support streaming responses"
            )
        return await asyncio.to_thread(self._send_sync, payload)

    def _send_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProviderTransportError(
                "GitHub Copilot HTTP transport received a non-JSON payload"
            ) from exc

        request = urllib_request.Request(
            self.endpoint,
            data=body,
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read()
        except urllib_error.HTTPError as exc:
            message = _format_http_error(exc, self._token)
            raise ProviderTransportError(message) from None
        except urllib_error.URLError as exc:
            reason = _redact_secret(str(getattr(exc, "reason", exc)), self._token)
            raise ProviderTransportError(
                "GitHub Copilot HTTP transport failed: {0}".format(reason)
            ) from None
        except TimeoutError as exc:
            raise ProviderTransportError(
                "GitHub Copilot HTTP transport timed out after {0} seconds".format(
                    self.timeout
                )
            ) from None

        try:
            text = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProviderTransportError(
                "GitHub Copilot HTTP transport returned non-UTF-8 response data"
            ) from exc

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderTransportError(
                "GitHub Copilot HTTP transport returned invalid JSON"
            ) from exc
        if not isinstance(data, dict):
            raise ProviderTransportError(
                "GitHub Copilot HTTP transport returned a non-object JSON response"
            )
        return data

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer {0}".format(self._token),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Openai-Intent": "conversation-edits",
            "x-initiator": self.initiator,
        }


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

        payload_model = _requested_model(request) or self.model
        if self.endpoint == "responses":
            return provider_request_to_openai_responses(
                request.provider_request,
                model=payload_model,
                instructions=self.instructions,
                stream=self.stream,
                metadata=self.metadata,
            )
        return provider_request_to_openai_chat(
            request.provider_request,
            model=payload_model,
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


class GitHubCopilotProvider(OpenAICompatibleProvider):
    """Thin OpenAI-compatible facade for GitHub Copilot payload tests."""

    def __init__(
        self,
        *,
        transport: ProviderTransport,
        model: str = DEFAULT_MODEL_ID,
        endpoint: str = "chat",
        instructions: Optional[str] = None,
        stream: bool = False,
        metadata: Optional[Mapping[str, Any]] = None,
        adapter: Optional[LLMEventAdapter] = None,
    ) -> None:
        provider_metadata = dict(metadata or {})
        provider_metadata.update(
            {
                "provider": DEFAULT_PROVIDER_ID,
                "provider_id": DEFAULT_PROVIDER_ID,
            }
        )
        super().__init__(
            model=model,
            transport=transport,
            endpoint=endpoint,
            instructions=instructions,
            stream=stream,
            metadata=provider_metadata,
            adapter=adapter,
        )

    def build_payload(self, request: RuntimeRequest) -> dict[str, Any]:
        """Project a request and apply GitHub Copilot request quirks."""

        payload = super().build_payload(request)
        _inject_copilot_noop_tool_fallback(payload, request)
        return payload

    def _transport_error_response(self, exc: BaseException) -> dict[str, Any]:
        response = super()._transport_error_response(exc)
        metadata = response.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["provider"] = DEFAULT_PROVIDER_ID
            metadata["provider_id"] = DEFAULT_PROVIDER_ID
        return response


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


def github_copilot_provider_from_env(
    *,
    model: str = DEFAULT_MODEL_ID,
    endpoint: str = "chat",
    instructions: Optional[str] = None,
    stream: bool = False,
    metadata: Optional[Mapping[str, Any]] = None,
    adapter: Optional[LLMEventAdapter] = None,
    timeout: float = 60,
    user_agent: str = "efp-runtime",
    initiator: str = "user",
    env: Optional[Mapping[str, str]] = None,
) -> GitHubCopilotProvider:
    """Create a GitHub Copilot provider using caller-supplied environment auth."""

    environ = os.environ if env is None else env
    token = _env_string(environ, "EFP_GITHUB_COPILOT_TOKEN") or _env_string(
        environ,
        "GITHUB_COPILOT_TOKEN",
    )
    if token is None:
        raise ProviderTransportError(
            "GitHub Copilot token is required; set EFP_GITHUB_COPILOT_TOKEN "
            "or GITHUB_COPILOT_TOKEN"
        )
    transport = GitHubCopilotHTTPTransport(
        token=token,
        base_url=_env_string(environ, "EFP_GITHUB_COPILOT_BASE_URL"),
        timeout=timeout,
        user_agent=user_agent,
        initiator=initiator,
    )
    return GitHubCopilotProvider(
        transport=transport,
        model=model,
        endpoint=endpoint,
        instructions=instructions,
        stream=stream,
        metadata=metadata,
        adapter=adapter,
    )


def _requested_model(request: RuntimeRequest) -> Optional[str]:
    requested_model = request.metadata.get("requested_model")
    if not isinstance(requested_model, str):
        return None
    requested_model = requested_model.strip()
    if not requested_model:
        return None
    return requested_model


def _inject_copilot_noop_tool_fallback(
    payload: dict[str, Any],
    request: RuntimeRequest,
) -> None:
    if request.provider_request.tools:
        return
    tools = payload.get("tools")
    if tools:
        return
    if not _provider_request_has_tool_call(request):
        return
    payload["tools"] = [_copilot_noop_tool_payload(payload)]
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata["copilot_noop_tool_fallback"] = True


def _provider_request_has_tool_call(request: RuntimeRequest) -> bool:
    for message in request.provider_request.messages:
        for part in message.parts:
            if part.tool_call is not None:
                return True
    return False


def _copilot_noop_tool_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if "input" in payload:
        return {
            "type": "function",
            "name": "_noop",
            "description": "No-op fallback for GitHub Copilot tool-call history.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    return {
        "type": "function",
        "function": {
            "name": "_noop",
            "description": "No-op fallback for GitHub Copilot tool-call history.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }


def _env_string(environ: Mapping[str, str], name: str) -> Optional[str]:
    value = environ.get(name)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _required_non_empty_string(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError("{0} must be a non-empty string".format(field_name))
    value = value.strip()
    if not value:
        raise ValueError("{0} must be a non-empty string".format(field_name))
    return value


def _normalize_base_url(base_url: Optional[str]) -> str:
    if base_url is None:
        return GitHubCopilotHTTPTransport.DEFAULT_BASE_URL
    base_url = _required_non_empty_string(base_url, "base_url")
    return base_url.rstrip("/")


def _format_http_error(exc: urllib_error.HTTPError, token: str) -> str:
    status = getattr(exc, "code", None)
    reason = getattr(exc, "reason", None) or getattr(exc, "msg", "")
    parts = ["GitHub Copilot HTTP transport failed"]
    if status is not None:
        parts.append("with status {0}".format(status))
    if reason:
        parts.append("({0})".format(_redact_secret(str(reason), token)))
    response_text = _read_http_error_body(exc)
    if response_text:
        parts.append("response: {0}".format(_redact_secret(response_text, token)))
    return " ".join(parts)


def _read_http_error_body(exc: urllib_error.HTTPError) -> str:
    try:
        raw_body = exc.read(2048)
    except Exception:
        return ""
    if not raw_body:
        return ""
    try:
        return raw_body.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _redact_secret(text: str, secret: str) -> str:
    if not text or not secret:
        return text
    return text.replace(secret, "[redacted]")


__all__ = [
    "GitHubCopilotHTTPTransport",
    "GitHubCopilotProvider",
    "OpenAICompatibleProvider",
    "ProviderTransport",
    "ProviderTransportError",
    "RecordingTransport",
    "TransportOutput",
    "github_copilot_provider_from_env",
]
