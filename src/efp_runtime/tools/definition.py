"""Tool definition primitives for EFP runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
import inspect
from typing import Any

from ..permissions import PermissionMetadata


class ValidationError(ValueError):
    """Raised when tool arguments do not match a tool schema."""


class ToolAbortSignal:
    """Synchronous opencode-style view of a tool cancellation signal."""

    def __init__(
        self,
        cancel_requested: Callable[[], bool | Awaitable[bool]] | None = None,
    ) -> None:
        self._cancel_requested = cancel_requested

    @property
    def aborted(self) -> bool:
        if self._cancel_requested is None:
            return False
        if _is_async_callable(self._cancel_requested):
            return False

        result = self._cancel_requested()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            return False
        return bool(result)


@dataclass(frozen=True)
class OutputPolicy:
    """Controls how tool output is serialized for model-visible context."""

    max_chars: int | None = None
    max_lines: int | None = None
    max_bytes: int | None = None
    truncation_direction: str = "head"
    archive_full_output: bool = True
    truncate: bool = True
    include_raw_output: bool = True


@dataclass(frozen=True)
class ToolContext:
    """Execution context passed to a tool implementation."""

    session_id: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str | None = None
    tool_name: str | None = None
    run_id: str | None = None
    iteration: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    messages: list[Any] = field(default_factory=list)
    agent: str | None = None
    metadata_updates: list[dict[str, Any]] = field(default_factory=list)
    cancel_requested: Callable[[], bool | Awaitable[bool]] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    ask_requester: Callable[..., Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    message_id: str | None = None

    def __post_init__(self) -> None:
        updates = list(self.metadata_updates)
        object.__setattr__(self, "metadata_updates", updates)
        object.__setattr__(self, "metadata", ToolMetadata(self.metadata, updates))
        object.__setattr__(self, "extra", dict(self.extra))
        object.__setattr__(self, "messages", list(self.messages))

    def to_metadata(self) -> dict[str, Any]:
        """Return metadata with first-class execution context mirrored into it."""

        metadata = dict(self.metadata or {})
        if self.message_id is not None and self.message_id != "":
            metadata["message_id"] = str(self.message_id)
        _set_missing_metadata(metadata, "tool_call_id", self.tool_call_id)
        _set_missing_metadata(metadata, "tool_name", self.tool_name)
        _set_missing_metadata(metadata, "run_id", self.run_id)
        _set_missing_metadata(metadata, "iteration", self.iteration)
        return metadata

    @property
    def sessionID(self) -> str | None:
        """opencode-style alias for ``session_id``."""

        return self.session_id

    @property
    def messageID(self) -> str | None:
        """opencode-style alias for the assistant message carrying this tool call."""

        if self.message_id is not None and self.message_id != "":
            return str(self.message_id)
        metadata_message_id = self.metadata.get("message_id")
        if metadata_message_id is not None and metadata_message_id != "":
            return str(metadata_message_id)
        return self.request_id

    @property
    def callID(self) -> str | None:
        """opencode-style alias for ``tool_call_id``."""

        return self.tool_call_id

    @property
    def abort(self) -> ToolAbortSignal:
        """opencode-style synchronous cancellation alias."""

        return ToolAbortSignal(self.cancel_requested)

    async def is_cancelled(self) -> bool:
        """Return whether the surrounding runtime has requested cancellation."""

        if self.cancel_requested is None:
            return False
        result = self.cancel_requested()
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    async def ask(self, request: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        """Dispatch an opencode-style ask hook without bypassing runtime permissions."""

        payload = dict(request or {})
        payload.update(kwargs)
        if self.ask_requester is not None:
            result = _call_ask_requester(self.ask_requester, payload, self)
            if inspect.isawaitable(result):
                result = await result
            return result

        requests = self.metadata.setdefault("ask_requests", [])
        if not isinstance(requests, list):
            requests = []
            self.metadata["ask_requests"] = requests
        requests.append(dict(payload))
        self.metadata["permission_request"] = dict(payload)
        self.metadata_updates.append(
            {
                "metadata": {
                    "permission_request": dict(payload),
                    "ask_requests": [dict(item) for item in requests],
                }
            }
        )
        return {"status": "recorded", "request": dict(payload)}


AsyncToolExecute = Callable[[dict[str, Any], ToolContext], Awaitable[Any]]


class _NoOpAwaitable:
    def __await__(self):
        if False:
            yield None
        return None


class ToolMetadata(dict[str, Any]):
    """Dict-compatible metadata that also accepts opencode-style metadata updates."""

    def __init__(
        self,
        initial: Mapping[str, Any] | None = None,
        updates: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(dict(initial or {}))
        self._updates = updates if updates is not None else []

    def __call__(self, update: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        payload = dict(update or {})
        payload.update(kwargs)
        self._updates.append(payload)
        return _NoOpAwaitable()


def _call_ask_requester(
    requester: Callable[..., Any],
    payload: dict[str, Any],
    context: ToolContext,
) -> Any:
    try:
        signature = inspect.signature(requester)
    except (TypeError, ValueError):
        return requester(payload, context)

    parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        and parameter.default is inspect.Parameter.empty
    ]
    if any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    ):
        return requester(payload, context)
    if len(parameters) >= 2:
        return requester(payload, context)
    if len(parameters) == 1:
        return requester(payload)
    return requester()


def _set_missing_metadata(metadata: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    existing = metadata.get(key)
    if existing is None or existing == "":
        metadata[key] = value


def _is_async_callable(callback: Callable[..., Any]) -> bool:
    if inspect.iscoroutinefunction(callback):
        return True
    call = getattr(callback, "__call__", None)
    return inspect.iscoroutinefunction(call)


@dataclass(frozen=True)
class ToolDef:
    """A registered runtime tool."""

    id: str
    description: str
    input_schema: Mapping[str, Any]
    execute: AsyncToolExecute
    permission: PermissionMetadata = field(default_factory=PermissionMetadata)
    output_policy: OutputPolicy = field(default_factory=OutputPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)
    runtime_metadata: dict[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "runtime_metadata", dict(self.runtime_metadata))

    def validate_args(self, args: Mapping[str, Any] | None) -> dict[str, Any]:
        return validate_args(self.input_schema, args or {})


def validate_args(schema: Mapping[str, Any], args: Mapping[str, Any]) -> dict[str, Any]:
    """Validate args against a small JSON-schema-style object schema."""

    if not isinstance(args, Mapping):
        raise ValidationError("Tool arguments must be an object.")

    schema_type = schema.get("type")
    if schema_type not in (None, "object"):
        raise ValidationError("Tool input schema must be an object schema.")

    normalized = dict(args)
    _validate_object_properties(None, normalized, schema)
    return normalized


def _validate_object_properties(
    name: str | None,
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> None:
    properties = schema.get("properties", {})
    if properties is None:
        properties = {}
    if not isinstance(properties, Mapping):
        raise ValidationError("Tool input schema properties must be an object.")

    required = schema.get("required", [])
    if required is None:
        required = []
    if not isinstance(required, list):
        raise ValidationError("Tool input schema required field must be a list.")

    missing = [property_name for property_name in required if property_name not in value]
    if missing:
        if name is None:
            raise ValidationError(f"Missing required argument(s): {', '.join(missing)}")
        raise ValidationError(
            f"Argument '{name}' missing required property/properties: {', '.join(missing)}"
        )

    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            if name is None:
                raise ValidationError(f"Unexpected argument(s): {', '.join(extra)}")
            raise ValidationError(
                f"Argument '{name}' has unexpected property/properties: {', '.join(extra)}"
            )

    for property_name, property_value in value.items():
        property_schema = properties.get(property_name)
        if property_schema is None:
            additional_properties = schema.get("additionalProperties")
            if not isinstance(additional_properties, Mapping):
                continue
            property_schema = additional_properties
        _validate_value(_field_name(name, property_name), property_value, property_schema)


def _validate_value(name: str, value: Any, schema: Mapping[str, Any]) -> None:
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        allowed = ", ".join(repr(item) for item in enum)
        raise ValidationError(f"Argument '{name}' must be one of: {allowed}")

    expected = schema.get("type")
    if expected is None:
        return
    if isinstance(expected, list):
        if any(_matches_type(value, item) for item in expected):
            _validate_nested_value(name, value, schema)
            return
        expected_text = " or ".join(str(item) for item in expected)
        raise ValidationError(f"Argument '{name}' must be {expected_text}.")
    if not _matches_type(value, expected):
        raise ValidationError(f"Argument '{name}' must be {expected}.")
    _validate_numeric_bounds(name, value, schema)
    _validate_nested_value(name, value, schema)


def _validate_nested_value(name: str, value: Any, schema: Mapping[str, Any]) -> None:
    if isinstance(value, Mapping):
        _validate_object_properties(name, value, schema)
        return

    if isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema is None:
            return
        if not isinstance(item_schema, Mapping):
            raise ValidationError(f"Argument '{name}' item schema must be an object.")
        for index, item in enumerate(value):
            _validate_value(f"{name}[{index}]", item, item_schema)


def _field_name(parent: str | None, child: str) -> str:
    if parent is None:
        return child
    return f"{parent}.{child}"


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True


def _validate_numeric_bounds(name: str, value: Any, schema: Mapping[str, Any]) -> None:
    minimum = schema.get("minimum")
    if (
        minimum is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        if value < minimum:
            raise ValidationError(
                f"Argument '{name}' must be greater than or equal to {minimum}."
            )
    maximum = schema.get("maximum")
    if (
        maximum is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        if value > maximum:
            raise ValidationError(
                f"Argument '{name}' must be less than or equal to {maximum}."
            )
