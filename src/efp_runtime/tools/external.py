"""Transport-neutral external tool bridge for Runtime v2."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import inspect
import re
import threading
from typing import Any, Protocol

from ..permissions import ALLOW, PermissionMetadata
from .definition import OutputPolicy, ToolContext, ToolDef
from .registry import ToolRegistry


_DEFAULT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
_SAFE_TOOL_ID_PATTERN = re.compile(r"[^A-Za-z0-9_.-]")


def _default_input_schema() -> dict[str, Any]:
    return deepcopy(_DEFAULT_INPUT_SCHEMA)


@dataclass(frozen=True)
class ExternalToolSpec:
    """Provider-declared external tool shape.

    Providers expose these specs without binding Runtime v2 to a specific
    plugin, network, or subprocess transport.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=_default_input_schema)
    permission: PermissionMetadata | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    output_policy: OutputPolicy | None = None


@dataclass(frozen=True)
class ExternalToolContext:
    """Runtime context passed across the external provider boundary."""

    session_id: str | None
    message_id: str | None
    tool_call_id: str | None
    workspace_root: str | None
    runtime_metadata: dict[str, Any]
    provider_name: str
    tool_name: str


class ExternalToolProvider(Protocol):
    """External tool provider contract.

    Providers should expose ``name`` as their stable provider id. The bridge
    also accepts ``provider_name`` at runtime for adapters that already use that
    spelling.
    """

    name: str

    def list_tools(
        self,
    ) -> Iterable[ExternalToolSpec] | Awaitable[Iterable[ExternalToolSpec]]:
        ...

    def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ExternalToolContext,
    ) -> Any | Awaitable[Any]:
        ...


def external_tool_defs(
    provider: ExternalToolProvider,
    *,
    prefix: str | None = None,
    allow_override: bool = False,
) -> list[ToolDef]:
    """Convert a provider's external tool specs into Runtime v2 ``ToolDef``s."""

    provider_name = _provider_name(provider)
    raw_specs = provider.list_tools()
    specs = _resolve_maybe_awaitable(raw_specs)
    if isinstance(specs, (str, bytes)) or not isinstance(specs, Iterable):
        raise TypeError("External provider list_tools() must return an iterable.")

    definitions: list[ToolDef] = []
    definition_indexes: dict[str, int] = {}
    for spec in specs:
        tool = _tool_def_from_external_spec(
            provider=provider,
            provider_name=provider_name,
            spec=spec,
            prefix=prefix,
        )
        existing_index = definition_indexes.get(tool.id)
        if existing_index is not None:
            if not allow_override:
                raise ValueError(f"External tool already defined: {tool.id}")
            definitions[existing_index] = tool
            continue
        definition_indexes[tool.id] = len(definitions)
        definitions.append(tool)
    return definitions


def register_external_tools(
    registry: ToolRegistry,
    providers: Iterable[ExternalToolProvider],
    *,
    allow_override: bool = False,
) -> list[str]:
    """Register external provider tools into an existing Runtime v2 registry."""

    registered: list[str] = []
    for provider in providers:
        for tool in external_tool_defs(provider, allow_override=allow_override):
            registry.register(tool, replace=allow_override)
            registered.append(tool.id)
    return registered


def _tool_def_from_external_spec(
    *,
    provider: ExternalToolProvider,
    provider_name: str,
    spec: ExternalToolSpec,
    prefix: str | None,
) -> ToolDef:
    external_tool_name = _required_text(spec.name, "External tool name")
    tool_id = _external_tool_id(
        provider_name=provider_name,
        tool_name=external_tool_name,
        prefix=prefix,
    )
    metadata = _copy_mapping(spec.metadata, "External tool metadata")
    metadata.update(
        {
            "external_tool": True,
            "external_provider": provider_name,
            "external_tool_name": external_tool_name,
        }
    )
    return ToolDef(
        id=tool_id,
        description=str(spec.description),
        input_schema=_copy_mapping(spec.input_schema, "External tool input_schema"),
        execute=_external_execute(
            provider=provider,
            provider_name=provider_name,
            external_tool_name=external_tool_name,
        ),
        permission=_external_permission(
            spec.permission,
            provider_name=provider_name,
            tool_name=external_tool_name,
        ),
        output_policy=_external_output_policy(spec.output_policy),
        metadata=metadata,
    )


def _external_execute(
    *,
    provider: ExternalToolProvider,
    provider_name: str,
    external_tool_name: str,
):
    async def execute(args: dict[str, Any], context: ToolContext) -> Any:
        external_context = _external_context(
            context,
            provider_name=provider_name,
            tool_name=external_tool_name,
        )
        result = provider.execute(
            external_tool_name,
            _copy_value(dict(args)),
            external_context,
        )
        if inspect.isawaitable(result):
            return await result
        return result

    return execute


def _external_context(
    context: ToolContext,
    *,
    provider_name: str,
    tool_name: str,
) -> ExternalToolContext:
    metadata = _copy_mapping(context.metadata or {}, "Tool context metadata")
    return ExternalToolContext(
        session_id=context.session_id,
        message_id=_optional_text(metadata.get("message_id")),
        tool_call_id=_optional_text(context.tool_call_id or metadata.get("tool_call_id")),
        workspace_root=_optional_text(metadata.get("workspace_root")),
        runtime_metadata=metadata,
        provider_name=provider_name,
        tool_name=tool_name,
    )


def _external_tool_id(
    *,
    provider_name: str,
    tool_name: str,
    prefix: str | None,
) -> str:
    sanitized_tool_name = _sanitize_tool_id_part(tool_name)
    if prefix is None:
        sanitized_prefix = _sanitize_tool_id_part(provider_name)
    elif prefix == "":
        sanitized_prefix = ""
    else:
        sanitized_prefix = _sanitize_tool_id_part(prefix)
    if not sanitized_prefix:
        return sanitized_tool_name
    return f"{sanitized_prefix}_{sanitized_tool_name}"


def _provider_name(provider: ExternalToolProvider) -> str:
    value = getattr(provider, "name", None)
    if value is None:
        value = getattr(provider, "provider_name", None)
    return _required_text(value, "External provider name")


def _sanitize_tool_id_part(value: Any) -> str:
    text = _required_text(value, "External tool id part")
    return _SAFE_TOOL_ID_PATTERN.sub("_", text)


def _required_text(value: Any, label: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _external_permission(
    permission: PermissionMetadata | None,
    *,
    provider_name: str,
    tool_name: str,
) -> PermissionMetadata:
    if permission is None:
        return PermissionMetadata(
            action=ALLOW,
            category="external",
            resource=f"{provider_name}/{tool_name}",
            risk="medium",
        )
    return PermissionMetadata(
        action=permission.action,
        reason=permission.reason,
        category=permission.category,
        resource=permission.resource,
        risk=permission.risk,
        data=_copy_mapping(permission.data, "Permission metadata data"),
    )


def _external_output_policy(policy: OutputPolicy | None) -> OutputPolicy:
    if policy is None:
        return OutputPolicy()
    return OutputPolicy(
        max_chars=policy.max_chars,
        max_lines=policy.max_lines,
        max_bytes=policy.max_bytes,
        truncation_direction=policy.truncation_direction,
        archive_full_output=policy.archive_full_output,
        truncate=policy.truncate,
        include_raw_output=policy.include_raw_output,
    )


def _copy_mapping(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping.")
    copied = _copy_value(dict(value))
    if isinstance(copied, dict):
        return copied
    return dict(value)


def _copy_value(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return value


def _resolve_maybe_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_value(value))
    return _run_awaitable_in_thread(value)


async def _await_value(value: Awaitable[Any]) -> Any:
    return await value


def _run_awaitable_in_thread(value: Awaitable[Any]) -> Any:
    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result_box["result"] = asyncio.run(_await_value(value))
        except BaseException as exc:  # noqa: BLE001 - propagate provider boundary errors.
            error_box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error_box:
        raise error_box["error"]
    return result_box.get("result")


__all__ = [
    "ExternalToolContext",
    "ExternalToolProvider",
    "ExternalToolSpec",
    "external_tool_defs",
    "register_external_tools",
]
