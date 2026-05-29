"""Gateway adapter for Runtime v2 native chat execution."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from src.config import DEFAULT_LLM_MODEL, config
from src.efp_runtime.event_bus import RuntimeEventBus
from src.efp_runtime.events import RuntimeEvent
from src.efp_runtime.llm.provider import (
    GitHubCopilotHTTPTransport,
    GitHubCopilotProvider,
    ProviderTransportError,
)
from src.efp_runtime.loop.runner import RuntimeLoopResult
from src.efp_runtime.runtime import AgentRuntime, RuntimeConfig
from src.efp_runtime.session.gateway_facade import (
    get_runtime_v2_session_manager,
    get_runtime_v2_session_store,
    runtime_v2_session_root,
)
from src.efp_runtime.session.models import MessagePartType


SUPPORTED_PROVIDER_KEYS = {"github_copilot", "github-copilot", "copilot"}
RUNTIME_V2_NATIVE_PROVIDER_ERROR = (
    "Runtime v2 native mode only supports GitHub Copilot. "
    "Set llm.provider to github_copilot, github-copilot, or copilot."
)


class RuntimeV2ChatError(RuntimeError):
    """Configuration or execution error surfaced by the Runtime v2 adapter."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        error_type: str = "runtime_v2_chat_error",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.details = dict(details or {})


async def run_runtime_v2_chat(
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
        config=_runtime_config(runtime_model, track_usage=track_usage),
        store=get_runtime_v2_session_store(),
        event_bus=event_bus,
        metadata={
            "gateway": "webchat",
            "request_path": request_path,
            "agent_id": agent_id,
            "agent_name": agent_name,
        },
    )

    forwarder: asyncio.Task | None = None
    subscription = None
    if stream_callback is not None:
        subscription = event_bus.subscribe(session_id=session_id)
        forwarder = asyncio.create_task(_forward_runtime_events(subscription, stream_callback))

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
        await get_runtime_v2_session_manager().record_runtime_result(
            session_id,
            result,
            request_id=request_id,
        )
    except ProviderTransportError as exc:
        raise RuntimeV2ChatError(
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

    return _result_payload(
        result,
        request_id=request_id,
        model=runtime_model,
    )


def _runtime_session_root() -> Path:
    return runtime_v2_session_root()


def _runtime_workspace_root() -> Path:
    return Path.home() / ".efp" / "workspace"


def _runtime_config(model: str, *, track_usage: bool) -> RuntimeConfig:
    return RuntimeConfig(
        workspace_root=_runtime_workspace_root(),
        default_provider_id="github-copilot",
        default_model=model,
        max_iterations=_resolve_max_iterations(),
        track_usage=track_usage,
    )


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
    return text or DEFAULT_LLM_MODEL


def _build_github_copilot_provider(model: str) -> GitHubCopilotProvider:
    llm_config = config.llm if isinstance(config.llm, dict) else {}
    provider_key = str(llm_config.get("provider") or "").strip()
    normalized_provider = provider_key.lower()
    if normalized_provider not in SUPPORTED_PROVIDER_KEYS:
        raise RuntimeV2ChatError(
            RUNTIME_V2_NATIVE_PROVIDER_ERROR,
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
        raise RuntimeV2ChatError(
            "GitHub Copilot token is required for Runtime v2 native mode; "
            "set llm.api_key, EFP_GITHUB_COPILOT_TOKEN, or GITHUB_COPILOT_TOKEN.",
            status_code=401,
            error_type="authentication_error",
            details={"provider": "github-copilot"},
        )

    transport = GitHubCopilotHTTPTransport(
        token=token,
        base_url=_env_string("EFP_GITHUB_COPILOT_BASE_URL") or _config_string(llm_config, "api_base"),
        user_agent="efp-runtime-v2",
        initiator="user",
    )
    return GitHubCopilotProvider(
        transport=transport,
        model=model,
        endpoint="chat",
        stream=False,
        metadata={"gateway": "webchat"},
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
            "runtime": "efp_runtime_v2",
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


async def _forward_runtime_events(subscription: Any, stream_callback: Any) -> None:
    try:
        async for event in subscription:
            payload = event.to_dict() if hasattr(event, "to_dict") else event
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
                "runtime": "efp_runtime_v2",
            }
        },
    }
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
    "RUNTIME_V2_NATIVE_PROVIDER_ERROR",
    "RuntimeV2ChatError",
    "run_runtime_v2_chat",
]
