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
import re
from typing import TYPE_CHECKING, Any, List, Optional, Protocol, Union
from urllib import error as urllib_error
from urllib import request as urllib_request

from .adapter import DefaultLLMEventAdapter, LLMEventAdapter
from .models import (
    DEFAULT_MODEL_ID,
    DEFAULT_PROVIDER_ID,
    SUPPORTED_COPILOT_MODEL_IDS,
    canonicalize_copilot_model_id,
)
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


class ProviderModelUnavailableError(ProviderTransportError):
    """Raised when GitHub Copilot rejects the requested model."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        available_models_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.available_models_text = available_models_text


DEFAULT_COPILOT_REASONING_EFFORT = "high"
DEFAULT_COPILOT_FALLBACK_MODEL = "gpt-5.5"
SUPPORTED_COPILOT_REASONING_EFFORTS = ("low", "medium", "high", "xhigh")


class GitHubCopilotHTTPTransport:
    """Standard-library HTTP JSON transport for GitHub Copilot Responses."""

    DEFAULT_BASE_URL = "https://api.githubcopilot.com"
    RESPONSES_PATH = "/responses"

    def __init__(
        self,
        *,
        token: str,
        base_url: Optional[str] = None,
        timeout: float = 60,
        user_agent: str = "GitHubCopilotChat/0.35.0",
        editor_version: str = "vscode/1.107.0",
        editor_plugin_version: str = "copilot-chat/0.35.0",
        integration_id: str = "vscode-chat",
        initiator: str = "agent",
    ) -> None:
        self._token = _required_non_empty_string(token, "token")
        self.base_url = _normalize_base_url(base_url)
        self.timeout = timeout
        self.user_agent = _required_non_empty_string(user_agent, "user_agent")
        self.editor_version = _required_non_empty_string(
            editor_version,
            "editor_version",
        )
        self.editor_plugin_version = _required_non_empty_string(
            editor_plugin_version,
            "editor_plugin_version",
        )
        self.integration_id = _required_non_empty_string(
            integration_id,
            "integration_id",
        )
        self.initiator = _required_non_empty_string(initiator, "initiator")
        self.endpoint = "{0}{1}".format(self.base_url, self.RESPONSES_PATH)

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
            response_text = _read_http_error_body(exc)
            model_unavailable_error = _model_unavailable_error_from_http_error(
                exc,
                response_text=response_text,
                token=self._token,
            )
            if model_unavailable_error is not None:
                raise model_unavailable_error from None
            message = _format_http_error(
                exc,
                self._token,
                response_text=response_text,
            )
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
            "Accept": "application/vnd.github.copilot-chat-preview+json",
            "User-Agent": self.user_agent,
            "Editor-Version": self.editor_version,
            "Editor-Plugin-Version": self.editor_plugin_version,
            "Copilot-Integration-Id": self.integration_id,
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
        reasoning_effort: Optional[str] = None,
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
        self.reasoning_effort = reasoning_effort
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
                reasoning_effort=self.reasoning_effort,
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
        endpoint: str = "responses",
        instructions: Optional[str] = None,
        stream: bool = False,
        metadata: Optional[Mapping[str, Any]] = None,
        reasoning_effort: str = DEFAULT_COPILOT_REASONING_EFFORT,
        fallback_model: str = DEFAULT_COPILOT_FALLBACK_MODEL,
        adapter: Optional[LLMEventAdapter] = None,
    ) -> None:
        if endpoint != "responses":
            raise ValueError("GitHub Copilot provider endpoint must be 'responses'")
        canonical_model = canonicalize_copilot_model_id(model)
        canonical_fallback_model = canonicalize_copilot_model_id(fallback_model)
        canonical_reasoning_effort = validate_copilot_reasoning_effort(reasoning_effort)
        provider_metadata = dict(metadata or {})
        provider_metadata.update(
            {
                "provider": DEFAULT_PROVIDER_ID,
                "provider_id": DEFAULT_PROVIDER_ID,
            }
        )
        super().__init__(
            model=canonical_model,
            transport=transport,
            endpoint=endpoint,
            instructions=instructions,
            stream=stream,
            metadata=provider_metadata,
            reasoning_effort=canonical_reasoning_effort,
            adapter=adapter,
        )
        self.fallback_model = canonical_fallback_model

    def build_payload(self, request: RuntimeRequest) -> dict[str, Any]:
        """Project a request and apply GitHub Copilot request quirks."""

        payload = super().build_payload(request)
        payload["model"] = canonicalize_copilot_model_id(payload.get("model"))
        if self.endpoint == "responses":
            payload["reasoning"] = {"effort": self.reasoning_effort}
        _inject_copilot_noop_tool_fallback(payload, request)
        return _sanitize_copilot_responses_payload(payload)

    async def invoke(self, request: RuntimeRequest) -> ProviderOutput:
        payload = self.build_payload(request)
        try:
            raw_output = self.transport.send(payload)
            if inspect.isawaitable(raw_output):
                raw_output = await raw_output
        except ProviderModelUnavailableError as exc:
            retry_payload = self._fallback_payload_for_model_unavailable(payload)
            if retry_payload is None:
                return self._transport_error_response(exc)
            try:
                raw_output = self.transport.send(retry_payload)
                if inspect.isawaitable(raw_output):
                    raw_output = await raw_output
            except Exception as retry_exc:
                return self._transport_error_response(retry_exc)
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

    def _fallback_payload_for_model_unavailable(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if self.stream:
            return None
        current_model = canonicalize_copilot_model_id(payload.get("model"))
        if current_model == self.fallback_model:
            return None
        retry_payload = deepcopy(dict(payload))
        retry_payload["model"] = self.fallback_model
        return retry_payload

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
    endpoint: str = "responses",
    instructions: Optional[str] = None,
    stream: bool = False,
    metadata: Optional[Mapping[str, Any]] = None,
    reasoning_effort: Optional[str] = None,
    fallback_model: Optional[str] = None,
    adapter: Optional[LLMEventAdapter] = None,
    timeout: float = 60,
    user_agent: str = "GitHubCopilotChat/0.35.0",
    initiator: str = "agent",
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
    configured_reasoning_effort = (
        reasoning_effort
        or _env_string(environ, "EFP_GITHUB_COPILOT_REASONING_EFFORT")
        or _env_string(environ, "EFP_LLM_REASONING_EFFORT")
        or DEFAULT_COPILOT_REASONING_EFFORT
    )
    configured_fallback_model = (
        fallback_model
        or _env_string(environ, "EFP_GITHUB_COPILOT_FALLBACK_MODEL")
        or DEFAULT_COPILOT_FALLBACK_MODEL
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
        reasoning_effort=configured_reasoning_effort,
        fallback_model=configured_fallback_model,
        adapter=adapter,
    )


def validate_copilot_reasoning_effort(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(_unsupported_reasoning_message(value))
    effort = value.strip().lower()
    if effort not in SUPPORTED_COPILOT_REASONING_EFFORTS:
        raise ValueError(_unsupported_reasoning_message(value))
    return effort


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


def _sanitize_copilot_responses_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    if "model" in payload:
        sanitized["model"] = payload["model"]
    if "input" in payload:
        sanitized["input"] = _sanitize_copilot_responses_input(payload.get("input"))
    tools = _sanitize_copilot_tools(payload.get("tools"))
    if tools:
        sanitized["tools"] = tools
    if "stream" in payload:
        sanitized["stream"] = payload["stream"]
    if payload.get("instructions") is not None:
        sanitized["instructions"] = payload["instructions"]
    if payload.get("reasoning") is not None:
        sanitized["reasoning"] = deepcopy(payload["reasoning"])
    return sanitized


def _sanitize_copilot_responses_input(input_value: Any) -> list[dict[str, Any]]:
    if not isinstance(input_value, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for item in input_value:
        sanitized.extend(_sanitize_copilot_responses_input_item(item))
    return sanitized


def _sanitize_copilot_responses_input_item(item: Any) -> list[dict[str, Any]]:
    if not isinstance(item, Mapping):
        return []
    item_type = item.get("type")
    if item_type == "function_call":
        return [_sanitize_copilot_function_call_item(item)]
    if item_type == "function_call_output":
        return [_sanitize_copilot_function_call_output_item(item)]
    if "content" not in item:
        return []

    role = item.get("role")
    if not isinstance(role, str) or not role:
        role = "user"
    content = item.get("content")
    if isinstance(content, str):
        return [{"role": role, "content": [{"type": "input_text", "text": content}]}]
    if not isinstance(content, list):
        return []

    sanitized: list[dict[str, Any]] = []
    buffered_content: list[dict[str, Any]] = []

    def flush_message() -> None:
        if not buffered_content:
            return
        sanitized.append({"role": role, "content": list(buffered_content)})
        buffered_content.clear()

    for content_item in content:
        if not isinstance(content_item, Mapping):
            continue
        content_type = content_item.get("type")
        if content_type == "function_call":
            flush_message()
            sanitized.append(_sanitize_copilot_function_call_item(content_item))
            continue
        if content_type == "function_call_output":
            flush_message()
            sanitized.append(_sanitize_copilot_function_call_output_item(content_item))
            continue
        projected = _sanitize_copilot_message_content_item(content_item)
        if projected is not None:
            buffered_content.append(projected)

    flush_message()
    return sanitized


def _sanitize_copilot_message_content_item(
    item: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    item_type = item.get("type")
    if item_type == "input_text":
        return {"type": "input_text", "text": _copilot_string(item.get("text", ""))}
    if item_type == "input_image" and "image_url" in item:
        return {"type": "input_image", "image_url": deepcopy(item["image_url"])}
    if item_type == "input_file":
        sanitized: dict[str, Any] = {"type": "input_file"}
        if item.get("file_id") is not None:
            sanitized["file_id"] = item["file_id"]
        else:
            if item.get("filename") is not None:
                sanitized["filename"] = item["filename"]
            if item.get("file_data") is not None:
                sanitized["file_data"] = item["file_data"]
        if len(sanitized) > 1:
            return sanitized
    return None


def _sanitize_copilot_function_call_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": _copilot_string(item.get("call_id", "")),
        "name": _copilot_string(item.get("name") or item.get("tool_name") or ""),
        "arguments": _copilot_arguments_text(item),
    }


def _sanitize_copilot_function_call_output_item(
    item: Mapping[str, Any],
) -> dict[str, Any]:
    output = item.get("output")
    if output is None:
        output = item.get("content")
    if output is None:
        output = item.get("error", "")
    return {
        "type": "function_call_output",
        "call_id": _copilot_string(item.get("call_id", "")),
        "output": _copilot_value_text(output),
    }


def _sanitize_copilot_tools(tools_value: Any) -> list[dict[str, Any]]:
    if not isinstance(tools_value, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for tool in tools_value:
        clean_tool = _sanitize_copilot_tool(tool)
        if clean_tool is not None:
            sanitized.append(clean_tool)
    return sanitized


def _sanitize_copilot_tool(tool: Any) -> Optional[dict[str, Any]]:
    if not isinstance(tool, Mapping):
        return None
    source: Mapping[str, Any] = tool
    function = tool.get("function")
    if isinstance(function, Mapping) and tool.get("name") is None:
        source = function

    name = source.get("name")
    if not isinstance(name, str) or not name:
        return None
    sanitized: dict[str, Any] = {
        "type": _copilot_string(tool.get("type") or "function"),
        "name": name,
    }
    if source.get("description") is not None:
        sanitized["description"] = source["description"]
    if source.get("parameters") is not None:
        sanitized["parameters"] = deepcopy(source["parameters"])
    return sanitized


def _copilot_arguments_text(item: Mapping[str, Any]) -> str:
    arguments = item.get("arguments")
    if arguments is not None:
        return _copilot_value_text(arguments)
    arguments_text = item.get("arguments_text")
    if arguments_text is not None:
        return _copilot_value_text(arguments_text)
    arguments_json = item.get("arguments_json")
    if arguments_json is not None:
        return _copilot_value_text(arguments_json)
    return ""


def _copilot_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (Mapping, list)):
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
    return str(value)


def _copilot_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


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


def _format_http_error(
    exc: urllib_error.HTTPError,
    token: str,
    *,
    response_text: str | None = None,
) -> str:
    status = getattr(exc, "code", None)
    reason = getattr(exc, "reason", None) or getattr(exc, "msg", "")
    parts = ["GitHub Copilot HTTP transport failed"]
    if status is not None:
        parts.append("with status {0}".format(status))
    if reason:
        parts.append("({0})".format(_redact_secret(str(reason), token)))
    if response_text is None:
        response_text = _read_http_error_body(exc)
    if response_text:
        parts.append("response: {0}".format(_redact_secret(response_text, token)))
    return " ".join(parts)


def _model_unavailable_error_from_http_error(
    exc: urllib_error.HTTPError,
    *,
    response_text: str,
    token: str,
) -> ProviderModelUnavailableError | None:
    status = getattr(exc, "code", None)
    if status != 400 or not response_text:
        return None
    error_message = _json_error_message(response_text)
    if error_message is None or not _is_model_unavailable_message(error_message):
        return None
    safe_message = _redact_secret(error_message, token)
    available_models_text = _available_models_text(safe_message)
    return ProviderModelUnavailableError(
        "GitHub Copilot model is not available: {0}".format(safe_message),
        status_code=status,
        available_models_text=available_models_text,
    )


def _json_error_message(response_text: str) -> str | None:
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, Mapping):
        return None

    error_value = data.get("error")
    if isinstance(error_value, Mapping):
        for key in ("message", "detail", "error"):
            value = error_value.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(error_value, str) and error_value.strip():
        return error_value.strip()
    for key in ("message", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_model_unavailable_message(message: str) -> bool:
    text = message.lower()
    return (
        "requested model is not available" in text
        or "model is not available" in text
    )


def _available_models_text(message: str) -> str | None:
    match = re.search(r"available models:\s*(.+)$", message, flags=re.IGNORECASE)
    if match is None:
        return None
    available_models = match.group(1).strip()
    return available_models or None


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


def _unsupported_reasoning_message(value: Any) -> str:
    supported = ", ".join(SUPPORTED_COPILOT_REASONING_EFFORTS)
    return "unsupported GitHub Copilot reasoning effort {0!r}; supported values: {1}".format(
        value,
        supported,
    )


__all__ = [
    "DEFAULT_COPILOT_FALLBACK_MODEL",
    "DEFAULT_COPILOT_REASONING_EFFORT",
    "GitHubCopilotHTTPTransport",
    "GitHubCopilotProvider",
    "OpenAICompatibleProvider",
    "ProviderModelUnavailableError",
    "ProviderTransport",
    "ProviderTransportError",
    "RecordingTransport",
    "SUPPORTED_COPILOT_MODEL_IDS",
    "SUPPORTED_COPILOT_REASONING_EFFORTS",
    "TransportOutput",
    "github_copilot_provider_from_env",
    "validate_copilot_reasoning_effort",
]
