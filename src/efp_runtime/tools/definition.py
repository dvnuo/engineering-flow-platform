"""Tool definition primitives for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..permissions import PermissionMetadata


class ValidationError(ValueError):
    """Raised when tool arguments do not match a tool schema."""


@dataclass(frozen=True)
class OutputPolicy:
    """Controls how tool output is serialized for model-visible context."""

    max_chars: int | None = None
    truncate: bool = True
    include_raw_output: bool = True


@dataclass(frozen=True)
class ToolContext:
    """Execution context passed to a tool implementation."""

    session_id: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


AsyncToolExecute = Callable[[dict[str, Any], ToolContext], Awaitable[Any]]


@dataclass(frozen=True)
class ToolDef:
    """A registered runtime v2 tool."""

    id: str
    description: str
    input_schema: Mapping[str, Any]
    execute: AsyncToolExecute
    permission: PermissionMetadata = field(default_factory=PermissionMetadata)
    output_policy: OutputPolicy = field(default_factory=OutputPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate_args(self, args: Mapping[str, Any] | None) -> dict[str, Any]:
        return validate_args(self.input_schema, args or {})


def validate_args(schema: Mapping[str, Any], args: Mapping[str, Any]) -> dict[str, Any]:
    """Validate args against a small JSON-schema-style object schema."""

    if not isinstance(args, Mapping):
        raise ValidationError("Tool arguments must be an object.")

    schema_type = schema.get("type")
    if schema_type not in (None, "object"):
        raise ValidationError("Tool input schema must be an object schema.")

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

    normalized = dict(args)
    missing = [name for name in required if name not in normalized]
    if missing:
        raise ValidationError(f"Missing required argument(s): {', '.join(missing)}")

    if schema.get("additionalProperties") is False:
        extra = sorted(set(normalized) - set(properties))
        if extra:
            raise ValidationError(f"Unexpected argument(s): {', '.join(extra)}")

    for name, value in normalized.items():
        property_schema = properties.get(name)
        if property_schema is None:
            continue
        _validate_value(name, value, property_schema)

    return normalized


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
            return
        expected_text = " or ".join(str(item) for item in expected)
        raise ValidationError(f"Argument '{name}' must be {expected_text}.")
    if not _matches_type(value, expected):
        raise ValidationError(f"Argument '{name}' must be {expected}.")


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
