"""Gateway adapter for EFP runtime native chat execution."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import math
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from src.config import DEFAULT_LLM_MODEL, PORTAL_MANAGED_RUNTIME_FIELDS, config
from src.gateway.runtime_event_projection import RuntimeEventProjector
from src.workspace_defaults import resolve_runtime_workspace
from src.efp_runtime.event_bus import RuntimeEventBus
from src.efp_runtime.events import RuntimeEvent
from src.efp_runtime.llm.provider import (
    DEFAULT_COPILOT_REASONING_EFFORT,
    DEFAULT_GITHUB_COPILOT_TIMEOUT_SECONDS,
    GitHubCopilotHTTPTransport,
    GitHubCopilotProvider,
    ProviderTransportError,
    validate_copilot_reasoning_effort,
)
from src.efp_runtime.llm.models import canonicalize_copilot_model_id
from src.efp_runtime.loop.runner import LoopStatus, RuntimeLoopResult
from src.efp_runtime.runtime import AgentRuntime, RuntimeConfig
from src.efp_runtime.session.gateway_facade import (
    get_runtime_session_manager,
    get_runtime_session_store,
    runtime_session_root,
)
from src.efp_runtime.session.models import MessagePartType
from src.utils.redaction import sanitize_exception_message


SUPPORTED_PROVIDER_KEYS = {"github_copilot", "github-copilot", "copilot"}
PORTAL_RUNTIME_PROFILE_SOURCE = "portal.runtime_profile"
RUNTIME_NATIVE_PROVIDER_ERROR = (
    "EFP runtime native mode only supports GitHub Copilot. "
    "Set llm.provider to github_copilot, github-copilot, or copilot."
)
DISABLED_TIMEOUT_VALUES = {"false", "off", "none", "disabled"}
TIMEOUT_SECONDS_ENV_KEYS = (
    "EFP_GITHUB_COPILOT_TIMEOUT_SECONDS",
    "EFP_LLM_TIMEOUT_SECONDS",
)
TIMEOUT_MS_ENV_KEYS = (
    "EFP_GITHUB_COPILOT_TIMEOUT_MS",
    "EFP_LLM_TIMEOUT_MS",
)


class RuntimeChatError(RuntimeError):
    """Configuration or execution error surfaced by the runtime adapter."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        error_type: str = "runtime_chat_error",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.details = dict(details or {})


async def run_runtime_chat(
    *,
    message: str,
    session_id: str,
    user_name: str | None = None,
    portal_user_id: str | None = None,
    portal_user_name: str | None = None,
    attached_images: list[str] | None = None,
    attachments: list[str] | None = None,
    transient_model_message: str | None = None,
    reasoning_replay: bool | None = None,
    stream_callback: Any = None,
    request_path: str = "/api/chat",
    execution_metadata: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    request_id: str | None = None,
    model: str | None = None,
    track_usage: bool = True,
) -> dict[str, Any]:
    """Run the production chat loop through ``efp_runtime.runtime.AgentRuntime``."""

    runtime_model = _resolve_model(model)
    provider = _build_github_copilot_provider(runtime_model)
    event_bus = RuntimeEventBus()
    runtime = AgentRuntime(
        provider=provider,
        config=_runtime_config(
            runtime_model,
            track_usage=track_usage,
            execution_metadata=execution_metadata,
        ),
        store=get_runtime_session_store(),
        event_bus=event_bus,
        metadata={
            "gateway": "runtime_api",
            "request_path": request_path,
            "agent_id": agent_id,
            "agent_name": agent_name,
        },
    )

    forwarder: asyncio.Task | None = None
    subscription = None
    if stream_callback is not None:
        subscription = event_bus.subscribe(session_id=session_id)
        forwarder = asyncio.create_task(
            _forward_runtime_events(
                subscription,
                stream_callback,
                projector=RuntimeEventProjector(
                    request_id=request_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    model=runtime_model,
                ),
            )
        )

    run_metadata = _run_metadata(
        request_path=request_path,
        request_id=request_id,
        user_name=user_name,
        portal_user_id=portal_user_id,
        portal_user_name=portal_user_name,
        attached_images=attached_images,
        attachments=attachments,
        transient_model_message=transient_model_message,
        reasoning_replay=reasoning_replay,
        execution_metadata=execution_metadata,
        agent_id=agent_id,
        agent_name=agent_name,
        model=runtime_model,
    )
    prompt = _compose_user_prompt(
        message=message,
        transient_model_message=transient_model_message,
        attached_images=attached_images,
    )

    try:
        result = await runtime.run(
            prompt,
            session_id=session_id,
            metadata=run_metadata,
        )
        await get_runtime_session_manager().record_runtime_result(
            session_id,
            result,
            request_id=request_id,
        )
    except ProviderTransportError as exc:
        raise RuntimeChatError(
            str(exc),
            status_code=401 if "token is required" in str(exc).lower() else 502,
            error_type="provider_transport_error",
            details={"provider": "github-copilot"},
        ) from exc
    finally:
        if subscription is not None:
            subscription.close()
        if forwarder is not None:
            await _await_forwarder_done(forwarder)

    payload = _result_payload(
        result,
        request_id=request_id,
        model=runtime_model,
    )
    if result.status == LoopStatus.ERROR:
        raise RuntimeChatError(
            payload.get("error") or "EFP runtime execution failed.",
            status_code=_runtime_error_status_code(result),
            error_type=payload.get("error_type") or "runtime_execution_error",
            details={
                "provider": "github-copilot",
                "runtime_status": result.status,
                "request_id": request_id,
            },
        )
    return payload


def _runtime_session_root() -> Path:
    return runtime_session_root()


def _runtime_workspace_root() -> Path:
    try:
        config_data = config.get_effective_config()
    except Exception:
        config_data = getattr(config, "_config", None)
    return resolve_runtime_workspace(config_data).resolve()


def _runtime_config(
    model: str,
    *,
    track_usage: bool,
    execution_metadata: Mapping[str, Any] | None = None,
    runtime_profile_config: Mapping[str, Any] | None = None,
) -> RuntimeConfig:
    managed_overlay_config = _active_managed_overlay_runtime_config()
    profile_config = (
        runtime_profile_config
        if isinstance(runtime_profile_config, Mapping)
        else _trusted_runtime_profile_config(execution_metadata)
    )
    kwargs: dict[str, Any] = {
        "workspace_root": _runtime_workspace_root(),
        "default_provider_id": "github-copilot",
        "default_model": model,
        "max_iterations": _resolve_max_iterations(),
        "track_usage": track_usage,
    }
    kwargs.update(_runtime_config_profile_kwargs(managed_overlay_config))
    kwargs.update(_runtime_config_profile_kwargs(profile_config))
    kwargs["workspace_root"] = _runtime_workspace_root()
    kwargs["default_provider_id"] = "github-copilot"
    kwargs["default_model"] = model
    if not (
        _mapping_has_key(managed_overlay_config, "track_usage")
        or _mapping_has_key(profile_config, "track_usage")
    ):
        kwargs["track_usage"] = track_usage

    try:
        return RuntimeConfig(**kwargs)
    except (TypeError, ValueError) as exc:
        raise RuntimeChatError(
            f"Invalid EFP runtime profile config: {exc}",
            status_code=400,
            error_type="invalid_runtime_config",
            details={"provider": "github-copilot"},
        ) from exc


def _active_managed_overlay_runtime_config() -> Mapping[str, Any] | None:
    try:
        effective_config = config.get_effective_config()
    except Exception:
        return None
    if not isinstance(effective_config, Mapping):
        return None
    # set_managed_overlay persists Portal-managed fields directly into config.yaml.
    # After a process restart the in-memory overlay metadata is empty, but those
    # safe top-level runtime fields still need to drive RuntimeConfig.
    if not any(field in effective_config for field in PORTAL_MANAGED_RUNTIME_FIELDS):
        return None
    return effective_config


def _trusted_runtime_profile_config(
    execution_metadata: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if not isinstance(execution_metadata, Mapping):
        return None
    runtime_profile = execution_metadata.get("runtime_profile")
    if not isinstance(runtime_profile, Mapping):
        return None
    if not _runtime_profile_metadata_is_trusted(runtime_profile, execution_metadata):
        return None
    profile_config = runtime_profile.get("config")
    return profile_config if isinstance(profile_config, Mapping) else None


def _runtime_profile_metadata_is_trusted(
    runtime_profile: Mapping[str, Any],
    execution_metadata: Mapping[str, Any],
) -> bool:
    if _source_value(runtime_profile.get("source")) == PORTAL_RUNTIME_PROFILE_SOURCE:
        return True
    if execution_metadata.get("trusted_control_plane") is True:
        return True
    if execution_metadata.get("_trusted_control_plane") is True:
        return True
    return _source_value(execution_metadata.get("source")) in {
        PORTAL_RUNTIME_PROFILE_SOURCE,
        "portal.control_plane",
    }


def _source_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _mapping_has_key(value: Mapping[str, Any] | None, key: str) -> bool:
    return isinstance(value, Mapping) and key in value


def _runtime_config_profile_kwargs(
    profile_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(profile_config, Mapping):
        return {}
    return {
        key: deepcopy(value)
        for key, value in profile_config.items()
        if key in PORTAL_MANAGED_RUNTIME_FIELDS
    }


def _resolve_max_iterations() -> int:
    value = config.session.get("max_iterations", 30)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 30
    return parsed if parsed > 0 else 30


def _resolve_model(model: str | None = None) -> str:
    configured = model or config.llm.get("model") or DEFAULT_LLM_MODEL
    text = str(configured).strip()
    try:
        return canonicalize_copilot_model_id(text or DEFAULT_LLM_MODEL)
    except ValueError as exc:
        raise RuntimeChatError(
            str(exc),
            status_code=400,
            error_type="invalid_model",
            details={"provider": "github-copilot"},
        ) from exc


def _build_github_copilot_provider(model: str) -> GitHubCopilotProvider:
    llm_config = config.llm if isinstance(config.llm, dict) else {}
    provider_key = str(llm_config.get("provider") or "").strip()
    normalized_provider = provider_key.lower()
    if normalized_provider not in SUPPORTED_PROVIDER_KEYS:
        raise RuntimeChatError(
            RUNTIME_NATIVE_PROVIDER_ERROR,
            status_code=400,
            error_type="unsupported_provider",
            details={"configured_provider": provider_key or None},
        )

    token = (
        _env_string("EFP_GITHUB_COPILOT_TOKEN")
        or _env_string("GITHUB_COPILOT_TOKEN")
        or _config_string(llm_config, "api_key")
    )
    if token is None:
        raise RuntimeChatError(
            "GitHub Copilot token is required for EFP runtime native mode; "
            "set llm.api_key, EFP_GITHUB_COPILOT_TOKEN, or GITHUB_COPILOT_TOKEN.",
            status_code=401,
            error_type="authentication_error",
            details={"provider": "github-copilot"},
        )

    transport = GitHubCopilotHTTPTransport(
        token=token,
        base_url=_env_string("EFP_GITHUB_COPILOT_BASE_URL") or _config_string(llm_config, "api_base"),
        timeout=_resolve_github_copilot_timeout(llm_config),
        user_agent="GitHubCopilotChat/0.35.0",
        initiator="agent",
    )
    return GitHubCopilotProvider(
        transport=transport,
        model=model,
        endpoint="responses",
        stream=True,
        metadata={"gateway": "runtime_api"},
        reasoning_effort=_resolve_reasoning_effort(llm_config),
    )


def _resolve_reasoning_effort(llm_config: Mapping[str, Any]) -> str:
    raw = (
        _env_string("EFP_GITHUB_COPILOT_REASONING_EFFORT")
        or _env_string("EFP_LLM_REASONING_EFFORT")
        or _config_string(llm_config, "reasoning_effort")
        or DEFAULT_COPILOT_REASONING_EFFORT
    )
    try:
        return validate_copilot_reasoning_effort(raw)
    except ValueError as exc:
        raise RuntimeChatError(
            str(exc),
            status_code=400,
            error_type="invalid_reasoning_effort",
            details={"provider": "github-copilot"},
        ) from exc


def _resolve_github_copilot_timeout(llm_config: Mapping[str, Any]) -> float | None:
    env_timeout = _timeout_from_env()
    if env_timeout is not None:
        value, unit, source = env_timeout
        return _parse_timeout_value(value, unit=unit, source=source)

    for field, unit in (
        ("timeout_seconds", "seconds"),
        ("timeout_ms", "ms"),
        ("timeout", "ms"),
        ("request_timeout_seconds", "seconds"),
    ):
        if field in llm_config:
            return _parse_timeout_value(
                llm_config.get(field),
                unit=unit,
                source="llm.{0}".format(field),
            )

    return DEFAULT_GITHUB_COPILOT_TIMEOUT_SECONDS


def _timeout_from_env() -> tuple[str, str, str] | None:
    for name in TIMEOUT_SECONDS_ENV_KEYS:
        value = _env_string(name)
        if value is not None:
            return value, "seconds", "env:{0}".format(name)
    for name in TIMEOUT_MS_ENV_KEYS:
        value = _env_string(name)
        if value is not None:
            return value, "ms", "env:{0}".format(name)
    return None


def _parse_timeout_value(value: Any, *, unit: str, source: str) -> float | None:
    if _timeout_is_disabled(value):
        return None
    if unit == "seconds":
        return _positive_timeout_seconds(value, source=source)
    if unit == "ms":
        return _positive_timeout_milliseconds(value, source=source) / 1000.0
    raise AssertionError("unsupported timeout unit: {0}".format(unit))


def _timeout_is_disabled(value: Any) -> bool:
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().lower() in DISABLED_TIMEOUT_VALUES
    return False


def _positive_timeout_seconds(value: Any, *, source: str) -> float:
    expected = "a positive number of seconds or false/off/none/disabled"
    if isinstance(value, bool):
        _raise_invalid_timeout(value, source, expected)
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = float(text)
        except ValueError:
            _raise_invalid_timeout(value, source, expected)
    else:
        _raise_invalid_timeout(value, source, expected)

    if not math.isfinite(parsed) or parsed <= 0:
        _raise_invalid_timeout(value, source, expected)
    return parsed


def _positive_timeout_milliseconds(value: Any, *, source: str) -> int:
    expected = "a positive integer number of milliseconds or false/off/none/disabled"
    if isinstance(value, bool):
        _raise_invalid_timeout(value, source, expected)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            _raise_invalid_timeout(value, source, expected)
        parsed = int(text, 10)
    else:
        _raise_invalid_timeout(value, source, expected)

    if parsed <= 0:
        _raise_invalid_timeout(value, source, expected)
    return parsed


def _raise_invalid_timeout(value: Any, source: str, expected: str) -> None:
    raise RuntimeChatError(
        "Invalid GitHub Copilot timeout value for {0}: expected {1}.".format(
            source,
            expected,
        ),
        status_code=400,
        error_type="invalid_timeout",
        details={
            "provider": "github-copilot",
            "source": source,
            "value": value,
        },
    )


def _env_string(name: str) -> str | None:
    value = os.environ.get(name)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _config_string(source: Mapping[str, Any], key: str) -> str | None:
    value = source.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _run_metadata(
    *,
    request_path: str,
    request_id: str | None,
    user_name: str | None,
    portal_user_id: str | None,
    portal_user_name: str | None,
    attached_images: list[str] | None,
    attachments: list[str] | None,
    transient_model_message: str | None,
    reasoning_replay: bool | None,
    execution_metadata: Mapping[str, Any] | None,
    agent_id: str | None,
    agent_name: str | None,
    model: str,
) -> dict[str, Any]:
    metadata = dict(execution_metadata or {})
    metadata.update(
        {
            "runtime": "efp_runtime",
            "runtime_type": "native",
            "path": request_path,
            "request_id": request_id,
            "user_name": user_name,
            "portal_user_id": portal_user_id,
            "portal_user_name": portal_user_name,
            "attached_image_count": len(attached_images or []),
            "attachments": list(attachments or []),
            "has_transient_model_message": bool(transient_model_message),
            "reasoning_replay": reasoning_replay,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "requested_model": model,
        }
    )
    return {key: value for key, value in metadata.items() if value is not None}


def _compose_user_prompt(
    *,
    message: str,
    transient_model_message: str | None,
    attached_images: list[str] | None,
) -> str:
    parts: list[str] = []
    transient = (transient_model_message or "").strip()
    user_text = (message or "").strip()
    if transient:
        parts.append(transient)
    if user_text and user_text not in {"[attachment]", "[image]"}:
        parts.append(user_text)
    elif user_text and not transient:
        parts.append(user_text)
    if attached_images:
        parts.append(
            "Image attachment data URI count: {0}. Use available attachment context "
            "from this prompt when answering.".format(len(attached_images))
        )
    return "\n\n".join(parts).strip()


async def _forward_runtime_events(
    subscription: Any,
    stream_callback: Any,
    *,
    projector: RuntimeEventProjector | None = None,
) -> None:
    try:
        async for event in subscription:
            payloads = (
                projector.project(event)
                if projector is not None
                else [event.to_dict() if hasattr(event, "to_dict") else event]
            )
            for payload in payloads:
                if hasattr(stream_callback, "put"):
                    await stream_callback.put(payload)
                elif callable(stream_callback):
                    maybe_result = stream_callback(payload)
                    if asyncio.iscoroutine(maybe_result):
                        await maybe_result
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def _await_forwarder_done(task: asyncio.Task) -> None:
    try:
        await asyncio.wait_for(task, timeout=1)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


def _result_payload(
    result: RuntimeLoopResult,
    *,
    request_id: str | None,
    model: str,
) -> dict[str, Any]:
    response_text, reasoning_text = _assistant_text_and_reasoning(result)
    runtime_events = [_event_to_dict(event) for event in result.runtime_events]
    error_message = _runtime_error_message(result, runtime_events)
    payload: dict[str, Any] = {
        "response": response_text,
        "content": response_text,
        "usage": dict(result.usage or {}),
        "events": runtime_events,
        "runtime_events": runtime_events,
        "request_id": request_id,
        "status": result.status,
        "_llm_debug": {
            "request": {
                "provider": "github-copilot",
                "model": model,
                "runtime": "efp_runtime",
            }
        },
    }
    if error_message:
        payload["error"] = error_message
        payload["error_type"] = _runtime_error_type(runtime_events)
    if reasoning_text:
        payload["reasoning"] = reasoning_text
    if result.pending_permission_request is not None:
        payload["pending_permission_request"] = result.pending_permission_request
    if result.pending_question_request is not None:
        payload["pending_question_request"] = result.pending_question_request
    if result.structured_output is not None:
        payload["structured_output"] = result.structured_output
    return payload


def _assistant_text_and_reasoning(result: RuntimeLoopResult) -> tuple[str, str]:
    message = result.final_assistant_message
    if message is None:
        return "", ""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    for part in message.parts:
        if part.type is MessagePartType.TEXT and part.text:
            text_parts.append(part.text)
        elif part.type is MessagePartType.REASONING and part.reasoning:
            reasoning_parts.append(part.reasoning)
    return "\n".join(text_parts).strip(), "\n".join(reasoning_parts).strip()


def _runtime_error_message(
    result: RuntimeLoopResult,
    runtime_events: list[dict[str, Any]],
) -> str:
    if result.status != LoopStatus.ERROR:
        return ""

    for event_type in ("llm.error", "error"):
        for event in reversed(runtime_events):
            if event.get("type") == event_type:
                message = _event_error_text(event)
                if message:
                    return message

    for event in reversed(runtime_events):
        message = _event_error_text(event)
        if message:
            return message

    message = result.final_assistant_message
    if message is not None:
        for part in reversed(message.parts):
            if part.type is MessagePartType.ERROR and part.text:
                return _sanitize_error_message(part.text)

    return "EFP runtime execution failed."


def _event_error_text(event: Mapping[str, Any]) -> str:
    payload = event.get("payload")
    payload_error = payload.get("error") if isinstance(payload, Mapping) else None
    for value in (
        payload_error,
        event.get("error"),
        event.get("message"),
    ):
        message = _sanitize_error_message(value)
        if message:
            return message
    return ""


def _sanitize_error_message(value: Any) -> str:
    if value in (None, ""):
        return ""
    return sanitize_exception_message(value).strip()


def _runtime_error_type(runtime_events: list[dict[str, Any]]) -> str:
    for event in reversed(runtime_events):
        payload = event.get("payload")
        if isinstance(payload, Mapping):
            error_type = payload.get("error_type")
            if isinstance(error_type, str) and error_type.strip():
                return error_type.strip()
        if event.get("type") == "llm.error":
            return "provider_error"
    return "runtime_execution_error"


def _runtime_error_status_code(result: RuntimeLoopResult) -> int:
    for event in result.runtime_events:
        event_type = getattr(event, "type", None)
        payload = getattr(event, "payload", {})
        if event_type == "llm.error":
            return 502
        if event_type == "error" and isinstance(payload, Mapping) and payload.get("phase") == "provider":
            return 502
    return 500


def _event_to_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, RuntimeEvent):
        return event.to_dict()
    if hasattr(event, "to_dict"):
        data = event.to_dict()
        return data if isinstance(data, dict) else {"value": data}
    if isinstance(event, dict):
        return dict(event)
    return {"value": str(event)}


__all__ = [
    "RUNTIME_NATIVE_PROVIDER_ERROR",
    "RuntimeChatError",
    "SUPPORTED_PROVIDER_KEYS",
    "run_runtime_chat",
]
